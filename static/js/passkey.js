// WebAuthn 通行密钥：注册、认证、登录。
// 依据 W3C WebAuthn Level 2：§5.4 CreationOptions / §5.5 RequestOptions。
// 浏览器 API 要求 challenge、user.id、credential.id 等为 ArrayBuffer，
// 而服务端下发的 options 与回传的凭证均为 base64url 字符串，这里负责双向转换。

// base64url 字符串 → ArrayBuffer
function b64urlToBuf(v) {
  v = v.replace(/-/g, '+').replace(/_/g, '/');
  while (v.length % 4) v += '=';
  const bin = atob(v);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

// ArrayBuffer → base64url 字符串
function bufToB64url(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// 把服务端下发的 options（base64url 字符串）转成 navigator.credentials 需要的二进制形态。
// 处理 §5.4 的 challenge / user.id / excludeCredentials，§5.5 的 challenge / allowCredentials。
function optionToPublicKey(opt) {
  const pk = Object.assign({}, opt);
  if (pk.challenge) pk.challenge = b64urlToBuf(pk.challenge);
  if (pk.user && pk.user.id) pk.user.id = b64urlToBuf(pk.user.id);
  if (pk.excludeCredentials) pk.excludeCredentials = pk.excludeCredentials.map(c => ({ ...c, id: b64urlToBuf(c.id) }));
  if (pk.allowCredentials) pk.allowCredentials = pk.allowCredentials.map(c => ({ ...c, id: b64urlToBuf(c.id) }));
  return pk;
}

// 把 PublicKeyCredential 序列化成服务端可解析的 JSON（二进制字段转 base64url）。
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

// 注册通行密钥：取 §5.4 CreationOptions → navigator.credentials.create → 提交验证。
async function registerPasskey(name, platform, modal) {
  try {
    const opt = await apiPost('/api/passkey/register/options', {});
    if (!opt.challenge) throw new Error(opt.error || 'No options');
    const cred = await navigator.credentials.create({ publicKey: optionToPublicKey(opt) });
    if (!cred) return;
    const body = JSON.parse(credToJson(cred));
    body.name = name;
    body.platform = platform;
    const r = await apiFetch('/api/passkey/register/verify', 'POST', body);
    if (r.ok) { modal?.classList.remove('show'); location.reload(); }
    else await uiAlert(r.error);
  } catch (e) { await uiAlert('Passkey registration failed: ' + e.message); }
}

// 认证通行密钥：取 §5.5 RequestOptions → navigator.credentials.get → 返回待验证凭证。
// 用于登录与删除验证两类场景。
async function authenticatePasskey(optionsUrl, email) {
  try {
    const optionsBody = email ? { email: email } : {};
    const opt = await apiPost(optionsUrl || '/api/passkey/auth/options', optionsBody);
    if (!opt.challenge) throw new Error(opt.error || 'No passkeys');
    const cred = await navigator.credentials.get({ publicKey: optionToPublicKey(opt) });
    if (!cred) return null;
    return JSON.parse(credToJson(cred));
  } catch (e) { await uiAlert('Passkey authentication failed: ' + e.message); return null; }
}

// 登录页：输入邮箱后用通行密钥完成认证。
document.getElementById('passkeyLoginBtn')?.addEventListener('click', async () => {
  const email = document.querySelector('input[name="email"]')?.value || '';
  const body = await authenticatePasskey('/api/passkey/auth/options', email);
  if (!body) return;
  const r = await apiPost('/api/passkey/auth/verify', body);
  if (r.ok) location.reload();
  else await uiAlert(r.error);
});
