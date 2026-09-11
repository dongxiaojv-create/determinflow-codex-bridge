"""Per-installation TLS and CLI discovery. Never copies login credentials."""
import datetime,hashlib,ipaddress,json,os,platform,shutil,uuid
from pathlib import Path
from urllib.parse import urlparse


def prepare_tls(data):
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Controller and Executor can register concurrently; initialize exactly once.
    import fcntl
    with (data/'setup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        folder=data/'tls'
        present=[(folder/name).is_file() for name in ('server.crt','server.key','trust.pem')]
        if any(present) and not all(present):
            raise ValueError('Incomplete Bridge TLS files; restore the plugin data before restarting')
        if not any(present):
            certificates(folder)
    return {'tls_dir':str(folder)}


def find_runtime(explicit=''):
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise ValueError('This preview supports macOS Apple Silicon only')
    pins=json.loads(Path(__file__).with_name('app-pins.json').read_text())
    candidates=[Path(explicit).expanduser()] if explicit else [
        Path(shutil.which('codex') or '/nonexistent/codex'),
        Path('/opt/homebrew/bin/codex'), Path('/usr/local/bin/codex'),
        Path.home()/'.local/bin/codex',
    ]
    for candidate in candidates:
        if not candidate.is_file():continue
        binary=candidate.resolve()
        # npm's executable is a JavaScript launcher; use its packaged native executable.
        if binary.suffix=='.js':
            npm=binary.parent.parent
            matches=list(npm.glob('vendor/*/bin/codex'))
            matches+=list(npm.parent.glob('codex-*/vendor/*/bin/codex'))
            matches+=list(npm.glob('node_modules/@openai/codex-*/vendor/*/bin/codex'))
        else:matches=[binary]
        for binary in matches:
            host=binary.with_name('codex-code-mode-host')
            if (binary.is_file() and os.access(binary,os.X_OK)
                and hashlib.sha256(binary.read_bytes()).hexdigest()==pins['runtime_sha256']
                and host.is_file() and os.access(host,os.X_OK)
                and hashlib.sha256(host.read_bytes()).hexdigest()==pins['runtime_code_mode_host_sha256']):
                return binary
    raise ValueError('需要官方 Codex CLI 0.153.4（macOS arm64，含 codex-code-mode-host）。请安装并运行 codex login；若安装在自定义目录，请在插件设置填写 codex_path。')


def proxy_environment(proxy):
    if proxy:
        parsed=urlparse(proxy)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('proxy must be an HTTP(S) proxy URL without credentials, query or path')
        try:parsed.port
        except ValueError:raise ValueError('Invalid proxy port') from None
    # Only the Runtime child receives these values; never change Core's proxy settings.
    result={'NO_PROXY':'127.0.0.1,localhost,::1'}
    if proxy:result.update(HTTP_PROXY=proxy,HTTPS_PROXY=proxy,ALL_PROXY=proxy)
    return result

def certificates(folder):
    import certifi
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
    folder.mkdir(parents=True,mode=0o700)
    now=datetime.datetime.now(datetime.timezone.utc);expiry=now+datetime.timedelta(days=365)
    key=rsa.generate_private_key(65537,2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Local Codex Bridge '+uuid.uuid4().hex)])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(False,False,False,False,False,True,True,False,False),True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()),False).sign(key,hashes.SHA256()))
    leaf_key=rsa.generate_private_key(65537,2048)
    leaf=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Local Codex Bridge')])).issuer_name(name)
        .public_key(leaf_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=5)).not_valid_after(expiry)
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
        .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()),False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),False).sign(key,hashes.SHA256()))
    (folder/'server.crt').write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (folder/'server.key').write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    (folder/'trust.pem').write_bytes(Path(certifi.where()).read_bytes()+b'\n'+ca.public_bytes(serialization.Encoding.PEM))
    # CA signing key is never persisted. Only this installation's leaf key and process-local trust remain.
    for path in folder.iterdir():path.chmod(0o600)
