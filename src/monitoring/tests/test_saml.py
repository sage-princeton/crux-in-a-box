import base64
import zlib
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from test_lifecycle import store
from test_web import ORIGIN

from review import digest
from web import create_app
from web_auth import LOGIN_COOKIE, SESSION_COOKIE


@pytest.mark.parametrize("empty_attributes", [False, True])
def test_real_signed_saml_login_rejects_tampering_and_replay(store, empty_attributes, caplog):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fixture IdP")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    settings = {
        "origin": ORIGIN,
        "bucket": "private-evidence",
        "idp": {
            "entityId": "https://idp.example.test",
            "singleSignOnService": {"url": "https://idp.example.test/login"},
            "x509cert": cert_pem,
        },
    }
    client = create_app(store, settings).test_client()
    response = client.get("/auth/login", base_url=ORIGIN)
    assert response.status_code == 302 and response.location.startswith(
        "https://idp.example.test/login?"
    )
    request_xml = etree.fromstring(
        zlib.decompress(
            base64.b64decode(parse_qs(urlsplit(response.location).query)["SAMLRequest"][0]), -15
        )
    )
    namespace = {"p": "urn:oasis:names:tc:SAML:2.0:protocol"}
    assert request_xml.find("p:NameIDPolicy", namespace).get("Format").endswith(":emailAddress")
    assert request_xml.find("p:RequestedAuthnContext", namespace) is None
    nonce = client.get_cookie(LOGIN_COOKIE, domain="incidents.example.test").value
    pending = store.get("LOGIN#" + digest(nonce), "STATE")
    rid = pending["request_id"]

    def stamp(delta):
        return (now + timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%SZ")

    assertion = f'''<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_fixture_assertion" Version="2.0" IssueInstant="{stamp(0)}">
      <saml:Issuer>https://idp.example.test</saml:Issuer>
      <saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">operator@example.test</saml:NameID>
        <saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer"><saml:SubjectConfirmationData InResponseTo="{rid}" NotOnOrAfter="{stamp(300)}" Recipient="{ORIGIN}/auth/callback"/></saml:SubjectConfirmation>
      </saml:Subject>
      <saml:Conditions NotBefore="{stamp(-60)}" NotOnOrAfter="{stamp(300)}"><saml:AudienceRestriction><saml:Audience>{ORIGIN}/auth/metadata</saml:Audience></saml:AudienceRestriction></saml:Conditions>
      <saml:AuthnStatement AuthnInstant="{stamp(0)}" SessionIndex="_session" SessionNotOnOrAfter="{stamp(300)}"><saml:AuthnContext><saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>
    </saml:Assertion>'''
    # AWS emits an empty statement when only Subject is mapped. It is invalid
    # under the SAML schema, even when no attributes are required by the app.
    attributes = (
        ""
        if empty_attributes
        else '<saml:Attribute Name="email"><saml:AttributeValue>operator@example.test</saml:AttributeValue></saml:Attribute>'
    )
    assertion = assertion.replace(
        "</saml:Assertion>",
        f"<saml:AttributeStatement>{attributes}</saml:AttributeStatement></saml:Assertion>",
    )
    signed = OneLogin_Saml2_Utils.add_sign(assertion, key_pem, cert_pem).decode()
    signed = etree.tostring(etree.fromstring(signed.encode()), encoding="unicode")
    xml = f'''<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" ID="_fixture_response" Version="2.0" IssueInstant="{stamp(0)}" Destination="{ORIGIN}/auth/callback" InResponseTo="{rid}">
      <saml:Issuer>https://idp.example.test</saml:Issuer><samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>{signed}</samlp:Response>'''

    def send(body):
        return client.post(
            "/auth/callback",
            base_url=ORIGIN,
            data={"SAMLResponse": base64.b64encode(body.encode()).decode(), "RelayState": nonce},
        )

    assert send(xml.replace("operator@example.test", "attacker@example.test")).status_code == 403
    assert not client.get_cookie(SESSION_COOKIE, domain="incidents.example.test")
    response = send(xml)
    if empty_attributes:
        assert response.status_code == 403
        assert "categories=['schema']" in caplog.text
        assert not client.get_cookie(SESSION_COOKIE, domain="incidents.example.test")
        return
    assert response.status_code == 302
    token = client.get_cookie(SESSION_COOKIE, domain="incidents.example.test").value
    session = store.get("SESSION#" + digest(token), "STATE")
    assert session["actor"]["id"] == "operator@example.test"
    client.set_cookie(LOGIN_COOKIE, nonce, domain="incidents.example.test")
    assert send(xml).status_code == 403
