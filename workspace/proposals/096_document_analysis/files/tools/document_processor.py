from PyPDF2 import PdfReader
import docx

def extract_pdf_text(filepath):
    with open(filepath, 'rb') as f:
        reader = PdfReader(f)
        return '\n'.join([page.extract_text() for page in reader.pages])

def extract_docx_text(filepath):
    doc = docx.Document(filepath)
    return '\n'.join([para.text for para in doc.paragraphs])