/* Photoshop document bridge. All generation controls live in the companion bar. */
const photoshop = require('photoshop');
const uxpStorage = require('uxp').storage;
const SERVER = 'http://127.0.0.1:47862';
const TOKEN_KEY = 'photocraft-genfill-token';
const ui = Object.fromEntries(['connection', 'settings', 'token', 'save-token', 'message'].map(id => [id, document.getElementById(id)]));
let busy = false;

function setMessage(message, error = false) {
  ui.message.textContent = message;
  ui.message.classList.toggle('error', error);
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
  } catch (error) {
    ui.connection.textContent = `Disconnected · ${String(error.message || error)}`;
    ui.connection.className = 'disconnected';
  }
}

async function readComposite(doc, onPixels) {
  const capture = await photoshop.core.executeAsModal(() => photoshop.imaging.getPixels({
      documentID: doc.id,
      sourceBounds: { left: 0, top: 0, right: doc.width, bottom: doc.height },
      colorSpace: 'RGB', colorProfile: 'sRGB IEC61966-2.1', componentSize: 8, applyAlpha: false
    }), { commandName: 'Read image for GenFill' });
    try {
      if (onPixels) await onPixels('Reading Photoshop pixel buffer');
      const image = capture.imageData;
      if (![3, 4].includes(image.components)) throw new Error('Photoshop returned unsupported pixel channels.');
      const pixels = await image.getData({ chunky: true, fullRange: true });
      if (onPixels) await onPixels('Encoding Photoshop pixel buffer');
      if (image.components === 3 && image.width === doc.width && image.height === doc.height &&
          capture.sourceBounds.left === 0 && capture.sourceBounds.top === 0) {
        return { image: bytesToBase64(pixels), image_components: 3,
                 image_format: 'raw', image_width: doc.width, image_height: doc.height };
      }
      if (image.components === 4 && image.width === doc.width && image.height === doc.height &&
          capture.sourceBounds.left === 0 && capture.sourceBounds.top === 0) {
        return { image: bytesToBase64(pixels), image_components: 4,
                 image_format: 'raw', image_width: doc.width, image_height: doc.height };
      }
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
}

async function readSelection(doc) {
  const bounds = doc.selection && doc.selection.bounds;
  if (!bounds) return {};
  const region = await photoshop.core.executeAsModal(() => photoshop.imaging.getSelection({ documentID: doc.id,
    sourceBounds: { left: 0, top: 0, right: doc.width, bottom: doc.height } }),
    { commandName: 'Read selection for GenFill' });
  try {
    const pixels = await region.imageData.getData({ chunky: true });
    return { selection_bounds: [bounds.left, bounds.top, bounds.right, bounds.bottom],
      selection_mask: bytesToBase64(pixels), selection_width: region.imageData.width,
      selection_height: region.imageData.height, selection_left: region.sourceBounds.left,
      selection_top: region.sourceBounds.top };
  } finally { region.imageData.dispose(); }
}

async function placeSelection(command, doc) {
  if (!doc || doc.id !== command.document) throw new Error('The Photoshop document changed. Select again.');
  const pixels = base64ToBytes(command.mask);
  if (pixels.length !== doc.width * doc.height) throw new Error('Selection mask has the wrong size.');
  await photoshop.core.executeAsModal(async () => {
    const data = await photoshop.imaging.createImageDataFromBuffer(pixels, {
      width: doc.width, height: doc.height, components: 1, colorSpace: 'Grayscale',
      colorProfile: 'Gray Gamma 2.2', chunky: false });
    try { await photoshop.imaging.putSelection({ documentID: doc.id, imageData: data, replace: true,
      commandName: 'GenFill selection' }); }
    finally { data.dispose(); }
  }, { commandName: 'GenFill selection' });
  return { message: 'Selected in Photoshop.' };
}

async function placeResult(result, sourceDocumentId, index, hideOriginal = false) {
  if (!result || !Number.isSafeInteger(result.width) || !Number.isSafeInteger(result.height) || result.width < 1 || result.height < 1) {
    throw new Error('Invalid result dimensions.');
  }
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
          const originals = hideOriginal ? Array.from(target.layers).filter(layer => layer.visible) : [];
          const copies = await opened.duplicateLayers([layers[0]], target);
          if (copies[0]) copies[0].name = `GenFill ${index + 1}`;
          for (const layer of originals) layer.visible = false;
          photoshop.app.activeDocument = target;
        } finally { await opened.closeWithoutSaving(); }
      } else {
        await opened.duplicate(`GenFill ${index + 1}`, true);
        await opened.closeWithoutSaving();
      }
    }, { commandName: 'Place GenFill result' });
    const message = target && target.width === result.width && target.height === result.height ?
      'Placed result as a new layer.' : 'Opened result as a new document.';
    setMessage(message);
    return { message };
  } finally {
    if (file) { try { await file.delete(); } catch (_) {} }
  }
}

async function reportProgress(id, stage, savedToken) {
  setMessage(stage);
  try {
    await fetch(`${SERVER}/v1/editor/progress`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-GenFill-Token': savedToken },
      body: JSON.stringify({ id, stage }) });
  } catch (_) { /* The capture or placement reply will report the real error. */ }
}

async function pollBar() {
  if (busy) return;
  const savedToken = await token();
  if (!savedToken) return;
  busy = true;
  try {
    const doc = photoshop.app.activeDocument;
    const selected = !!(doc && doc.selection && doc.selection.bounds);
    const params = `document=${doc ? doc.id : 0}&width=${doc ? doc.width : 0}&height=${doc ? doc.height : 0}&selection=${selected ? 1 : 0}`;
    const response = await fetch(`${SERVER}/v1/editor/next?${params}`, {
      headers: { 'X-GenFill-Token': savedToken }
    });
    if (!response.ok) throw new Error(`Companion returned ${response.status}.`);
    const { command } = await response.json();
    ui.connection.textContent = 'Connected · use the companion bar';
    ui.connection.className = 'connected';
    if (!command) return;
    let reply;
    try {
      if (command.type === 'capture') {
        if (!doc) throw new Error('Open a Photoshop document first.');
        await reportProgress(command.id, 'Reading Photoshop pixels', savedToken);
        reply = { result: { document: doc.id,
          ...await readComposite(doc, stage => reportProgress(command.id, stage, savedToken)),
          ...await readSelection(doc) } };
        await reportProgress(command.id, 'Sending the image to the companion', savedToken);
      } else if (command.type === 'selection') {
        reply = { result: await placeSelection(command, doc) };
      } else if (command.type === 'place') {
        await reportProgress(command.id, 'Placing result in Photoshop', savedToken);
        reply = { result: await placeResult(command.image, command.document, command.index || 0,
                                           !!command.hide_original) };
      } else throw new Error('Unknown companion command.');
    } catch (error) { reply = { error: String(error.message || error) }; setMessage(reply.error, true); }
    const sent = await fetch(`${SERVER}/v1/editor/reply`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-GenFill-Token': savedToken },
      body: JSON.stringify({ id: command.id, ...reply }) });
    if (!sent.ok) throw new Error(`Companion reply failed: ${sent.status}.`);
  } catch (error) {
    ui.connection.textContent = `Disconnected · ${String(error.message || error)}`;
    ui.connection.className = 'disconnected';
  } finally { busy = false; }
}

ui['save-token'].addEventListener('click', async () => {
  const value = ui.token.value.trim();
  if (!value) { setMessage('Enter a companion token.', true); return; }
  try {
    await uxpStorage.secureStorage.setItem(TOKEN_KEY, value);
    ui.token.value = '';
    setMessage('Token saved. Checking connection…');
    await checkConnection();
  } catch (error) {
    setMessage(`Could not save token: ${String(error.message || error)}`, true);
  }
});
checkConnection();
setInterval(pollBar, 700);
