import pandas as pd
import matplotlib.pyplot as plt

def analyze_data(file_path):
    data = pd.read_csv(file_path)
    summary = data.describe()
    plt.figure()
    data.hist()
    plt.savefig('output.png')
    return summary.to_dict()