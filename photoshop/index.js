/* PhotoCraft's local Photoshop bridge. Runs in the UXP panel, not in Node. */
const photoshop = require('photoshop');
const uxpStorage = require('uxp').storage;
const SERVER = 'http://127.0.0.1:47862';
const TOKEN_KEY = 'photocraft-genfill-token';
const ui = Object.fromEntries(['connection', 'settings', 'token', 'save-token', 'mode', 'prompt', 'count', 'generate', 'message', 'results', 'result-list'].map(id => [id, document.getElementById(id)]));
let results = [];
let sourceDocumentId = null;
let busy = false;

function setMessage(message, error = false) {
  ui.message.textContent = message;
  ui.message.classList.toggle('error', error);
}

function setBusy(value) {
  busy = value;
  ui.generate.disabled = value;
  ui['result-list'].querySelectorAll('button').forEach(button => { button.disabled = value; });
}

function bytesToBase64(bytes) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const pieces = [];
  for (let offset = 0; offset < bytes.length; offset += 16383) {
    const end = Math.min(offset + 16383, bytes.length);
    let part = '';
    for (let i = offset; i < end; i += 3) {
      const n = (bytes[i] << 16) | ((i + 1 < end ? bytes[i + 1] : 0) << 8) | (i + 2 < end ? bytes[i + 2] : 0);
      part += alphabet[n >>> 18] + alphabet[(n >>> 12) & 63] + (i + 1 < end ? alphabet[(n >>> 6) & 63] : '=') + (i + 2 < end ? alphabet[n & 63] : '=');
    }
    pieces.push(part);
  }
  return pieces.join('');
}

function base64ToBytes(value) {
  const encoded = value.replace(/^data:image\/png;base64,/, '').replace(/\s/g, '');
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(encoded) || encoded.length % 4 !== 0) throw new Error('Companion returned invalid PNG data.');
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  const output = new Uint8Array(encoded.length / 4 * 3 - (encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0));
  let cursor = 0;
  for (let i = 0; i < encoded.length; i += 4) {
    const n = (alphabet.indexOf(encoded[i]) << 18) | (alphabet.indexOf(encoded[i + 1]) << 12) | ((encoded[i + 2] === '=' ? 0 : alphabet.indexOf(encoded[i + 2])) << 6) | (encoded[i + 3] === '=' ? 0 : alphabet.indexOf(encoded[i + 3]));
    output[cursor++] = n >>> 16;
    if (encoded[i + 2] !== '=') output[cursor++] = (n >>> 8) & 255;
    if (encoded[i + 3] !== '=') output[cursor++] = n & 255;
  }
  return output;
}

async function token() {
  const value = await uxpStorage.secureStorage.getItem(TOKEN_KEY);
  return value ? String.fromCharCode(...value) : '';
}

async function checkConnection() {
  try {
    const saved = await token();
    const response = await fetch(`${SERVER}/v1/status`, { headers: saved ? { 'X-GenFill-Token': saved } : {} });
    const data = await response.json();
    if (!response.ok || !data.connected) throw new Error('Companion unavailable');
    ui.connection.textContent = data.paired ? `Connected · ${data.version || ''}` : 'Connected · pair token';
    ui.connection.className = data.paired ? 'connected' : 'disconnected';
  } catch (_) {
    ui.connection.textContent = 'Disconnected';
    ui.connection.className = 'disconnected';
  }
}

async function readComposite(doc) {
  return photoshop.core.executeAsModal(async () => {
    const capture = await photoshop.imaging.getPixels({
      documentID: doc.id,
      sourceBounds: { left: 0, top: 0, right: doc.width, bottom: doc.height },
      colorSpace: 'RGB', colorProfile: 'sRGB IEC61966-2.1', componentSize: 8, applyAlpha: false
    });
    try {
      const image = capture.imageData;
      const pixels = await image.getData({ chunky: true, fullRange: true });
      if (![3, 4].includes(image.components)) throw new Error('Photoshop returned unsupported pixel channels.');
      const full = new Uint8Array(doc.width * doc.height * 4);
      const left = capture.sourceBounds.left, top = capture.sourceBounds.top;
      for (let y = 0; y < image.height; y++) for (let x = 0; x < image.width; x++) {
        const source = (y * image.width + x) * image.components;
        const destination = ((top + y) * doc.width + left + x) * 4;
        full[destination] = pixels[source];
        full[destination + 1] = pixels[source + 1];
        full[destination + 2] = pixels[source + 2];
        full[destination + 3] = image.components === 4 ? pixels[source + 3] : 255;
      }
      return { image: bytesToBase64(full), image_width: doc.width, image_height: doc.height,
               image_components: 4, image_format: 'raw' };
    } finally { capture.imageData.dispose(); }
  }, { commandName: 'Read image for GenFill' });
}

async function generate() {
  if (busy) return;
  const prompt = ui.prompt.value.trim();
  const savedToken = await token();
  if (!savedToken) { ui.settings.open = true; setMessage('Paste and save the companion token first.', true); return; }
  if (!prompt) { setMessage('Enter a prompt first.', true); return; }
  const mode = ui.mode.value;
  const count = Number(ui.count.value);
  const source = photoshop.app.activeDocument;
  if (mode === 'edit' && !source) { setMessage('Open a Photoshop document to edit.', true); return; }
  setBusy(true);
  results = [];
  ui.results.hidden = true;
  ui['result-list'].textContent = '';
  sourceDocumentId = mode === 'edit' ? source.id : null;
  try {
    setMessage(mode === 'edit' ? 'Reading document pixels…' : 'Generating…');
    const body = { mode, prompt, count };
    if (mode === 'edit') Object.assign(body, await readComposite(source));
    setMessage('Generating…');
    const response = await fetch(`${SERVER}/v1/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-GenFill-Token': savedToken }, body: JSON.stringify(body)
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.error || `Companion error ${response.status}`);
    if (!Array.isArray(payload.results) || !payload.results.length) throw new Error('Companion returned no images.');
    results = payload.results;
    renderResults();
    setMessage(`Generated ${results.length} result${results.length === 1 ? '' : 's'}. Choose one to place.`);
  } catch (error) {
    setMessage(String(error.message || error), true);
  } finally { setBusy(false); checkConnection(); }
}

function renderResults() {
  ui.results.hidden = false;
  ui['result-list'].textContent = '';
  results.forEach((result, index) => {
    const row = document.createElement('div'); row.className = 'result';
    const preview = document.createElement('img');
    preview.alt = `Result ${index + 1}`;
    preview.src = `data:image/png;base64,${result.image}`;
    const meta = document.createElement('span'); meta.className = 'meta';
    meta.textContent = `${index + 1} · ${result.width} × ${result.height}`;
    const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Place';
    button.addEventListener('click', () => placeResult(index));
    row.append(preview, meta, button);
    ui['result-list'].appendChild(row);
  });
}

async function placeResult(index) {
  if (busy) return;
  const result = results[index];
  if (!result || !Number.isSafeInteger(result.width) || !Number.isSafeInteger(result.height) || result.width < 1 || result.height < 1) {
    setMessage('Invalid result dimensions.', true); return;
  }
  setBusy(true);
  let file;
  try {
    setMessage('Opening result in Photoshop…');
    const folder = await uxpStorage.localFileSystem.getTemporaryFolder();
    file = await folder.createFile(`genfill-${Date.now()}-${index}.png`, { overwrite: true });
    await file.write(base64ToBytes(result.image).buffer, { format: uxpStorage.formats.binary });
    const target = sourceDocumentId && Array.from(photoshop.app.documents).find(doc => doc.id === sourceDocumentId);
    await photoshop.core.executeAsModal(async () => {
      const opened = await photoshop.app.open(file);
      if (target && target.width === result.width && target.height === result.height) {
        try {
          const layers = opened.layers;
          if (!layers.length) throw new Error('Result image has no layer.');
          const copies = await opened.duplicateLayers([layers[0]], target);
          if (copies[0]) copies[0].name = `GenFill ${index + 1}`;
          photoshop.app.activeDocument = target;
        } finally { await opened.closeWithoutSaving(); }
      } else {
        await opened.duplicate(`GenFill ${index + 1}`, true);
        await opened.closeWithoutSaving();
      }
    }, { commandName: 'Place GenFill result' });
    setMessage(target && target.width === result.width && target.height === result.height ? 'Placed result as a new layer.' : 'Opened result as a new document.');
  } catch (error) {
    setMessage(String(error.message || error), true);
  } finally {
    if (file) { try { await file.delete(); } catch (_) {} }
    setBusy(false);
  }
}

ui['save-token'].addEventListener('click', async () => {
  const value = ui.token.value.trim();
  if (!value) { setMessage('Enter a companion token.', true); return; }
  await uxpStorage.secureStorage.setItem(TOKEN_KEY, new Uint8Array([...value].map(char => char.charCodeAt(0))));
  ui.token.value = '';
  ui.settings.open = false;
  setMessage('Token saved.');
  checkConnection();
});
ui.generate.addEventListener('click', generate);
checkConnection();
