// Decode the actual printed PDFs: DOM/SVG checks alone missed a CSP-blocked fill.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { BinaryBitmap, Code128Reader, DecodeHintType, HybridBinarizer, RGBLuminanceSource } = require('@zxing/library');

const output = path.resolve(process.argv[2] || path.join(__dirname, '../../test-results/browser'));
const expected = JSON.parse(fs.readFileSync(path.join(output, 'printed-barcodes.json'), 'utf8'));
assert(expected.length > 0, 'The browser must print at least one location');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'sps-printed-labels-'));
const dpi = 300;
const pixels = mm => Math.round(mm * dpi / 25.4);
const hints = new Map([[DecodeHintType.TRY_HARDER, true]]);

function readPage(pdf, pageNumber) {
  const prefix = path.join(temporary, 'page');
  execFileSync('pdftoppm', ['-f', String(pageNumber), '-singlefile', '-r', String(dpi), '-gray', pdf, prefix]);
  const data = fs.readFileSync(prefix + '.pgm');
  // Poppler emits a binary PGM with this header and one byte per pixel.
  const header = data.subarray(0, 100).toString('ascii').match(/^P5\s+(\d+)\s+(\d+)\s+255\s/);
  assert(header, 'Unexpected Poppler raster format');
  const width = Number(header[1]), height = Number(header[2]);
  const luminance = data.subarray(header[0].length);
  assert.equal(luminance.length, width * height, 'Incomplete PDF raster');
  return new RGBLuminanceSource(new Uint8ClampedArray(luminance), width, height);
}

function decode(source, barcode, description) {
  let actual;
  try {
    actual = new Code128Reader().decode(new BinaryBitmap(new HybridBinarizer(source)), hints).getText();
  } catch (error) {
    throw new Error(`${description}: printed barcode is unreadable (${barcode})`, { cause: error });
  }
  assert.equal(actual, barcode, `${description}: printed barcode must identify the correct location`);
  process.stdout.write(`${description}: decoded ${actual}\n`);
}

function checkPdf(filename, format) {
  const pdf = path.join(output, filename);
  const info = execFileSync('pdfinfo', [pdf], { encoding: 'utf8' });
  const pages = Number(info.match(/^Pages:\s+(\d+)/m)?.[1]);
  const size = info.match(/^Page size:\s+([\d.]+) x ([\d.]+) pts/m);
  assert(size, `${filename}: page dimensions missing`);
  const dimensions = size.slice(1).map(value => Number(value) * 25.4 / 72);
  const expectedDimensions = format === 'a4' ? [210, 297] : [100, 60];
  dimensions.forEach((value, i) => assert(Math.abs(value - expectedDimensions[i]) < 0.4, `${filename}: incorrect paper size`));
  const perPage = format === 'a4' ? 8 : 1;
  assert.equal(pages, Math.ceil(expected.length / perPage), `${filename}: unexpected blank or missing pages`);
  let currentPage = 0, source;
  expected.forEach((barcode, index) => {
    const pageNumber = Math.floor(index / perPage) + 1;
    if (pageNumber !== currentPage) {
      source = readPage(pdf, pageNumber);
      currentPage = pageNumber;
    }
    // A4: 10 mm margins, two columns, 57 mm labels and 5 mm gaps.
    // Keep the crop just inside the cut line; retain the barcode's quiet zone.
    const slot = index % perPage;
    const crop = format === 'a4'
      ? source.crop(pixels(10.5 + (slot % 2) * 97.5), pixels(10.5 + Math.floor(slot / 2) * 62), pixels(91.5), pixels(56))
      : source;
    decode(crop, barcode, `${format} label ${index + 1}`);
  });
  execFileSync('pdftoppm', ['-f', '1', '-singlefile', '-r', '130', '-png', pdf, path.join(output, `printed-labels-${format}`)]);
}

try {
  checkPdf('sps-labels-test.pdf', 'a4');
  checkPdf('sps-labels-thermal-test.pdf', 'thermal');
  process.stdout.write('PRINTED PDF BARCODES PASSED\n');
} finally {
  fs.rmSync(temporary, { recursive: true, force: true });
}
