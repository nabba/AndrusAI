import matplotlib.pyplot as plt

def create_line_chart(x, y, labels):
    plt.figure(figsize=(10, 5))
    plt.plot(x, y, label=labels['line'])
    plt.title(labels['title'])
    plt.xlabel(labels['xlabel'])
    plt.ylabel(labels['ylabel'])
    plt.legend()
    plt.savefig('chart.png')