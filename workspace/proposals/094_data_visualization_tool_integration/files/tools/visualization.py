import matplotlib.pyplot as plt
import seaborn as sns

def create_barplot(data, x, y, title):
    plt.figure(figsize=(10,6))
    sns.barplot(x=x, y=y, data=data)
    plt.title(title)
    return plt

def save_plot(plt, filename):
    plt.savefig(filename)
    plt.close()