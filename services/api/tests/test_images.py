"""Contract photos: one JPEG or PNG is one page, converted to a PDF and reviewed as a scan."""

import struct

import pymupdf
import pytest

from app.pipeline import UserError, contract_pdf, sniff
from app.config import Settings
from pdfgen import PAGES, make_pdf


def _image(fmt: str = "png", dpi: int = 100) -> bytes:
    """A real page of contract text, rendered to an image: what a phone photo or a scanner gives."""
    src = pymupdf.open(stream=make_pdf([PAGES[1]]), filetype="pdf")
    data = src[0].get_pixmap(dpi=dpi).tobytes(fmt)
    src.close()
    return data


def _post(client, data: bytes, name: str, mime: str = "image/png"):
    return client.post("/api/reviews", files={"file": (name, data, mime)})


def test_png_with_text_is_reviewed_as_a_one_page_scan(client, services):
    png = _image("png")
    r = _post(client, png, "lease-page.png")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pages"] == 1 and body["filename"] == "lease-page.png"
    purposes = [c["purpose"] for c in services.llm.calls]
    assert purposes[0] == "transcribe"  # no text layer: the scanned path
    sent = services.llm.calls[0]["parts"][0]
    assert sent.mime_type == "application/pdf" and sent.data[:5] == b"%PDF-"  # the model gets the PDF
    # the stored document is the converted PDF, which the reader shows
    stored = client.get(f"/api/reviews/{body['id']}/pdf")
    assert stored.status_code == 200 and stored.headers["content-type"] == "application/pdf"
    doc = pymupdf.open(stream=stored.content, filetype="pdf")
    pix = pymupdf.Pixmap(png)
    assert doc.page_count == 1 and doc[0].get_text().strip() == ""
    # the page is the image at its own size: its pixels at its own resolution (here 100 dpi, so A4 again)
    assert pix.xres == 100
    assert doc[0].rect.width == pytest.approx(pix.width * 72 / pix.xres, abs=1)
    assert doc[0].rect.height == pytest.approx(pix.height * 72 / pix.yres, abs=1)
    for f in body["findings"]:  # boxes come from the model on the page image, in page coordinates
        assert f["page"] == 1 and all(0 <= v <= 1 for b in f["boxes"] for v in b)


def test_jpeg_is_accepted_and_named_by_bytes_not_by_name(client):
    r = _post(client, _image("jpeg"), "scan.pdf", "application/pdf")  # a JPEG that claims to be a PDF
    assert r.status_code == 200, r.text
    assert r.json()["pages"] == 1


def test_text_renamed_png_is_refused_with_the_plain_message(client):
    r = _post(client, b"Clause 1. The Vendor may do anything it likes.\n" * 20, "contract.png")
    assert r.status_code == 415
    err = r.json()["error"]
    assert err["code"] == "not_a_pdf" and err["message"].startswith("That file is not a PDF, JPEG or PNG.")


@pytest.mark.parametrize("head,name", [
    (b"RIFF\x10\x00\x00\x00WEBPVP8L", "WEBP"),
    (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1", "HEIC"),
])
def test_webp_and_heic_are_refused_with_what_to_do(client, head, name):
    r = _post(client, head + b"\x00" * 64, "photo." + name.lower())
    assert r.status_code == 415
    msg = r.json()["error"]["message"]
    assert f"We cannot read {name} images yet" in msg and "JPEG or PNG" in msg


def test_damaged_image_and_size_limits(client):
    r = _post(client, b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "broken.png")
    assert r.status_code == 422 and r.json()["error"]["code"] == "unreadable_image"
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 10)
    assert _post(client, big, "big.png").json()["error"]["code"] == "file_too_large"


def test_pixel_bomb_is_refused_before_decoding():
    # a valid PNG header that claims 20,000 x 20,000 pixels (400 MP)
    ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    import zlib
    chunk = b"IHDR" + ihdr
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk))
    with pytest.raises(UserError) as e:
        contract_pdf(png, Settings())
    assert e.value.code == "image_too_large"


def test_sniff():
    assert sniff(b"%PDF-1.7") == "pdf" and sniff(b"\n %PDF-1.4") == "pdf"
    assert sniff(_image("jpeg")) == "jpeg" and sniff(_image("png")) == "png"
    assert sniff(b"PK\x03\x04") is None and sniff(b"") is None


def test_company_policies_still_need_a_pdf(client):
    r = client.post("/api/company/docs", files={"file": ("policy.png", _image("png"), "image/png")})
    assert r.status_code in (401, 403, 415)
    if r.status_code == 415:
        assert r.json()["error"]["code"] == "not_a_pdf"


def test_phone_photo_rotation_is_applied():
    """A phone stores a portrait photo as landscape pixels plus an EXIF 'rotate 90' tag."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 200), False)
    pix.clear_with(255)
    jpg = pix.tobytes("jpeg")
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1) + struct.pack("<HHII", 0x0112, 3, 1, 6) \
        + struct.pack("<I", 0)
    app1 = b"Exif\x00\x00" + tiff
    rotated = jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpg[2:]
    pdf, kind = contract_pdf(rotated, Settings())
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    assert kind == "jpeg" and page.rect.height > page.rect.width  # upright, portrait
