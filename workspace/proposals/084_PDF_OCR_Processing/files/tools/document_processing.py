import PyPDF2
import pytesseract
from PIL import Image
import io

class DocumentProcessor:
    def extract_pdf_text(self, file_bytes: bytes) -> str:
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            return '\n'.join([page.extract_text() for page in reader.pages])
        except Exception as e:
            return f"PDF extraction failed: {str(e)}"
            
    def extract_image_text(self, file_bytes: bytes) -> str:
        try:
            image = Image.open(io.BytesIO(file_bytes))
            return pytesseract.image_to_string(image)
        except Exception as e:
            return f"OCR failed: {str(e)}"