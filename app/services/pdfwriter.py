"""Ko'p sahifali PDF'ni diskka bosqichma-bosqich yozish (har sahifa — JPEG rasm).

Pillow barcha sahifalarni xotirada ushlaydi (1500 sahifa = bir necha GB). Bu yozuvchi esa har
sahifani darhol faylga yozadi — xotira sarfi bitta sahifa qadar.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image


class JpegPdfWriter:
    def __init__(self, path: Path, dpi: int = 150, quality: int = 85) -> None:
        self.f = open(path, "wb")
        self.dpi, self.quality = dpi, quality
        self.offsets: list[int] = []   # obyekt raqami → fayldagi joyi
        self.page_ids: list[int] = []
        self.f.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        # 1 — katalog, 2 — sahifalar ro'yxati: oxirida yoziladi, raqamlarini band qilamiz
        self.offsets = [0, 0]

    def _obj(self, body: bytes) -> int:
        self.offsets.append(self.f.tell())
        num = len(self.offsets)
        self.f.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")
        return num

    def _obj_at(self, num: int, body: bytes) -> None:
        self.offsets[num - 1] = self.f.tell()
        self.f.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")

    def add(self, img: Image.Image) -> None:
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, "JPEG", quality=self.quality, optimize=True)
        jpg = buf.getvalue()
        w_pt, h_pt = img.width * 72 / self.dpi, img.height * 72 / self.dpi
        img_id = self._obj(
            f"<< /Type /XObject /Subtype /Image /Width {img.width} /Height {img.height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg)} >>\nstream\n".encode()
            + jpg + b"\nendstream")
        content = f"q {w_pt:.3f} 0 0 {h_pt:.3f} 0 0 cm /Im0 Do Q".encode()
        content_id = self._obj(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
        page_id = self._obj(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt:.3f} {h_pt:.3f}] "
            f"/Resources << /XObject << /Im0 {img_id} 0 R >> >> /Contents {content_id} 0 R >>".encode())
        self.page_ids.append(page_id)

    def close(self) -> None:
        kids = " ".join(f"{i} 0 R" for i in self.page_ids)
        self._obj_at(2, f"<< /Type /Pages /Kids [{kids}] /Count {len(self.page_ids)} >>".encode())
        self._obj_at(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        xref = self.f.tell()
        n = len(self.offsets) + 1
        self.f.write(f"xref\n0 {n}\n0000000000 65535 f \n".encode())
        for off in self.offsets:
            self.f.write(f"{off:010d} 00000 n \n".encode())
        self.f.write(f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        self.f.close()

    @property
    def pages(self) -> int:
        return len(self.page_ids)
