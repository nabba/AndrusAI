import matplotlib.pyplot as plt
import seaborn as sns

def plot_data(data, x_label, y_label, title):
    plt.figure(figsize=(10, 6))
    sns.lineplot(data=data)
    plt.xlabel(x_label)
    plt.ylabel(y_label)
    plt.title(title)
    plt.show()