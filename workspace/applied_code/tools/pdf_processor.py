import PyPDF2
import pytesseract
from pdf2image import convert_from_path

def extract_text(pdf_path):
    try:
        # Try regular text extraction first
        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            text = '\n'.join([page.extract_text() for page in reader.pages])
            
        if not text.strip():
            # Fall back to OCR for scanned PDFs
            images = convert_from_path(pdf_path)
            text = '\n'.join([pytesseract.image_to_string(image) for image in images])
            
        return text
    except Exception as e:
        return f"Error processing PDF: {str(e)}"