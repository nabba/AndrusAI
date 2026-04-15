import PyPDF2
import docx2txt
import os

def process_document(file_path):
    if file_path.endswith('.pdf'):
        with open(file_path, 'rb') as file:
            reader = PyPDF2.PdfFileReader(file)
            text = ''.join([reader.getPage(i).extractText() for i in range(reader.numPages)])
    elif file_path.endswith('.docx'):
        text = docx2txt.process(file_path)
    elif file_path.endswith('.txt'):
        with open(file_path, 'r') as file:
            text = file.read()
    else:
        raise ValueError('Unsupported file type')
    return text