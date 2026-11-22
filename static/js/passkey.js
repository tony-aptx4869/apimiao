// WebAuthn 通行密钥：注册、认证、登录。兼容 Apple / Microsoft / Google / YubiKey。
function b64urlToBuf(v) {
  v = v.replace(/-/g, '+').replace(/_/g, '/');
  while (v.length % 4) v += '=';
  const bin = atob(v);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}
function bufToB64url(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function optionToPublicKey(opt) {
  const pk = Object.assign({}, opt);
  ['challenge', 'user'].forEach(k => {
    if (pk[k] && pk[k].id) pk[k].id = b64urlToBuf(pk[k].id);
    else if (pk[k]) pk[k] = b64urlToBuf(pk[k]);
  });
  if (pk.excludeCredentials) pk.excludeCredentials = pk.excludeCredentials.map(c => ({...c, id: b64urlToBuf(c.id)}));
  if (pk.allowCredentials) pk.allowCredentials = pk.allowCredentials.map(c => ({...c, id: b64urlToBuf(c.id)}));
  return pk;
}
function credToJson(cred) {
  const o = {
    id: cred.id,
    rawId: bufToB64url(cred.rawId),
    type: cred.type,
    response: {
      authenticatorData: bufToB64url(cred.response.authenticatorData),
      clientDataJSON: bufToB64url(cred.response.clientDataJSON),
      signature: cred.response.signature ? bufToB64url(cred.response.signature) : undefined,
      userHandle: cred.response.userHandle ? bufToB64url(cred.response.userHandle) : undefined,
      attestationObject: cred.response.attestationObject ? bufToB64url(cred.response.attestationObject) : undefined
    },
    clientExtensionResults: cred.getClientExtensionResults()
  };
  if (cred.response.transports) o.response.transports = cred.response.transports;
  return JSON.stringify(o);
}

async function registerPasskey(name, platform, modal) {
  try {
    const opt = await apiPost('/api/passkey/register/options', {});
    if (!opt.challenge) throw new Error(opt.error || 'No options');
    const cred = await navigator.credentials.create({publicKey: optionToPublicKey(opt)});
    if (!cred) return;
    const body = JSON.parse(credToJson(cred));
    body.name = name;
    body.platform = platform;
    const r = await apiFetch('/api/passkey/register/verify', 'POST', body);
    if (r.ok) { modal?.classList.remove('show'); location.reload(); }
    else alert(r.error);
  } catch (e) { alert('Passkey registration failed: ' + e.message); }
}

async function authenticatePasskey(optionsUrl, email) {
  try {
    const optionsBody = email ? {email: email} : {};
    const opt = await apiPost(optionsUrl || '/api/passkey/auth/options', optionsBody);
    if (!opt.challenge) throw new Error(opt.error || 'No passkeys');
    const cred = await navigator.credentials.get({publicKey: optionToPublicKey(opt)});
    if (!cred) return null;
    const body = JSON.parse(credToJson(cred));
    body.credential_id_hex = Array.from(new Uint8Array(cred.rawId)).map(b => b.toString(16).padStart(2, '0')).join('');
    return body;
  } catch (e) { alert('Passkey authentication failed: ' + e.message); return null; }
}

document.getElementById('passkeyLoginBtn')?.addEventListener('click', async () => {
  const email = document.querySelector('input[name="email"]')?.value || '';
  const body = await authenticatePasskey('/api/passkey/auth/options', email);
  if (!body) return;
  const r = await apiPost('/api/passkey/auth/verify', body);
  if (r.ok) location.reload();
  else alert(r.error);
});
