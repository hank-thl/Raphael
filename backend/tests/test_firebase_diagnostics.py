import pytest
from firebase_admin import auth

from app.core.firebase import FirebaseAuthService, InvalidCredential


@pytest.mark.parametrize('error,reason', [
    (auth.InvalidIdTokenError('Token used too early. private-detail'), 'clock_skew'),
    (auth.InvalidIdTokenError('Firebase ID token has incorrect "aud" claim private-detail'), 'project_mismatch'),
    (auth.InvalidIdTokenError('Firebase ID token has incorrect "iss" claim private-detail'), 'project_mismatch'),
    (auth.InvalidIdTokenError('Could not verify signature private-detail'), 'invalid_signature'),
    (auth.ExpiredIdTokenError('private-detail', cause=ValueError('private-detail')), 'expired'),
    (auth.RevokedIdTokenError('private-detail'), 'revoked'),
    (auth.UserDisabledError('private-detail'), 'user_disabled'),
    (auth.UserNotFoundError('private-detail'), 'user_not_found'),
    (ValueError('private-detail'), 'invalid_token'),
])
def test_rejected_credentials_log_only_fixed_reason_and_keep_revocation_checks(error, reason, caplog):
    service = object.__new__(FirebaseAuthService)
    service.auth = auth
    service.app = object()
    def verifier(token, **kwargs):
        assert token == 'private-token'
        assert kwargs == {'app': service.app, 'check_revoked': True, 'clock_skew_seconds': 5}
        raise error
    with caplog.at_level('WARNING'), pytest.raises(InvalidCredential):
        service._verify(verifier, 'private-token', 'verify_id_token')
    assert f'stage=verify_id_token reason={reason}' in caplog.text
    assert 'private-token' not in caplog.text
    assert 'private-detail' not in caplog.text


@pytest.fixture(scope='module')
def signing_keys():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from google.auth.crypt import RSASigner
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption())
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                              serialization.PublicFormat.SubjectPublicKeyInfo)
    return RSASigner.from_string(private_pem, key_id='unit-key'), public_pem


@pytest.mark.parametrize('issued_offset,expiry_offset,accepted', [
    (3, 3600, True),
    (10, 3600, False),
    (-3600, -10, False),
])
def test_small_clock_tolerance_still_verifies_signed_token_and_time_bounds(
    signing_keys, monkeypatch, issued_offset, expiry_offset, accepted,
):
    import datetime
    from google.auth import jwt, _helpers
    fixed_time = datetime.datetime(2026, 1, 1)
    monkeypatch.setattr(_helpers, 'utcnow', lambda: fixed_time)
    now = _helpers.datetime_to_secs(fixed_time)
    signer, public_pem = signing_keys
    token = jwt.encode(signer, {'sub': 'unit-user', 'aud': 'unit-project',
                               'iat': now + issued_offset, 'exp': now + expiry_offset})
    service = object.__new__(FirebaseAuthService)
    service.auth = auth
    service.app = object()
    def verifier(value, **kwargs):
        assert kwargs['check_revoked'] is True
        return jwt.decode(value, certs={'unit-key': public_pem}, audience='unit-project',
                          clock_skew_in_seconds=kwargs['clock_skew_seconds'])
    if accepted:
        assert service._verify(verifier, token, 'verify_id_token')['sub'] == 'unit-user'
    else:
        with pytest.raises(InvalidCredential):
            service._verify(verifier, token, 'verify_id_token')


@pytest.mark.parametrize('failure', ['signature', 'audience'])
def test_clock_tolerance_never_bypasses_signature_or_audience(signing_keys, monkeypatch, failure):
    import datetime
    from google.auth import jwt, _helpers
    fixed_time = datetime.datetime(2026, 1, 1)
    monkeypatch.setattr(_helpers, 'utcnow', lambda: fixed_time)
    now = _helpers.datetime_to_secs(fixed_time)
    signer, public_pem = signing_keys
    token = jwt.encode(signer, {'aud': 'wrong-project' if failure == 'audience' else 'unit-project',
                               'iat': now + 3, 'exp': now + 3600}).decode()
    if failure == 'signature':
        header, payload, signature = token.split('.')
        signature = ('B' if signature[0] == 'A' else 'A') + signature[1:]
        token = '.'.join([header, payload, signature])
    service = object.__new__(FirebaseAuthService)
    service.auth = auth
    service.app = object()
    def verifier(value, **kwargs):
        return jwt.decode(value, certs={'unit-key': public_pem}, audience='unit-project',
                          clock_skew_in_seconds=kwargs['clock_skew_seconds'])
    with pytest.raises(InvalidCredential):
        service._verify(verifier, token, 'verify_id_token')
