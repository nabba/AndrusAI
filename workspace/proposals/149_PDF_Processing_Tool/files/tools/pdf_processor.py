from PyPDF2 import PdfReader
from pdfminer.high_level import extract_text

class PDFProcessor:
    def extract_text(self, file_path, method='pypdf'):
        try:
            if method == 'pypdf':
                reader = PdfReader(file_path)
                return ' '.join([page.extract_text() for page in reader.pages])
            else:
                return extract_text(file_path)
        except Exception as e:
            return f'PDF processing error: {str(e)}'
