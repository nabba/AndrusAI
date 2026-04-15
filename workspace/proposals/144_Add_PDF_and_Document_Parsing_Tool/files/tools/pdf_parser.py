import PyMuPDF

def parse_pdf(file_path):
    doc = PyMuPDF.open(file_path)
    text = ''
    for page in doc:
        text += page.get_text()
    return text