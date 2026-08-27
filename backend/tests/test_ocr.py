from app.ocr import detect_ocr


def test_detect_ocr_shape() -> None:
    info = detect_ocr()
    assert info.engine in ("ocrmac", "tesseract", "none")
    if info.engine == "none":
        assert info.languages == []
    else:
        assert info.languages
