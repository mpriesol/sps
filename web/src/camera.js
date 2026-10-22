import { BrowserMultiFormatReader } from '@zxing/browser';
import { coverCrop } from './domain.js';

// Decode exactly the central image visible through object-fit: cover.
// The capture keeps the source resolution, including on portrait cameras.
export class PreviewBarcodeReader extends BrowserMultiFormatReader {
  constructor(video, hints, options) {
    super(hints, options);
    this.video = video;
    this.cropCanvas = document.createElement('canvas');
  }

  decodeFromCanvas(canvas) {
    const { x, y, width, height } = coverCrop(canvas.width, canvas.height,
      this.video.clientWidth, this.video.clientHeight);
    this.cropCanvas.width = Math.round(width);
    this.cropCanvas.height = Math.round(height);
    const context = this.cropCanvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(canvas, x, y, width, height, 0, 0, this.cropCanvas.width, this.cropCanvas.height);
    return super.decodeFromCanvas(this.cropCanvas);
  }
}
