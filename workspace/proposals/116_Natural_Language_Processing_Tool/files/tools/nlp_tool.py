import nltk
import spacy

class NLPTool:
    def __init__(self):
        self.nlp = spacy.load('en_core_web_sm')
        nltk.download('punkt')

    def tokenize(self, text):
        return nltk.word_tokenize(text)

    def pos_tagging(self, text):
        return nltk.pos_tag(nltk.word_tokenize(text))

    def named_entity_recognition(self, text):
        doc = self.nlp(text)
        return [(ent.text, ent.label_) for ent in doc.ents]

# Example usage
# nlp_tool = NLPTool()
# print(nlp_tool.tokenize('This is a sample text.'))