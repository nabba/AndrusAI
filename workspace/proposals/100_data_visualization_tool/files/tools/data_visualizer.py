import matplotlib.pyplot as plt
import pandas as pd

def create_visualization(data, chart_type='bar', title='', xlabel='', ylabel=''):
    """
    Generate various chart types from input data
    Supported types: bar, line, scatter, histogram
    """
    df = pd.DataFrame(data)
    fig, ax = plt.subplots()
    
    if chart_type == 'bar':
        df.plot.bar(ax=ax)
    elif chart_type == 'line':
        df.plot.line(ax=ax)
    elif chart_type == 'scatter':
        df.plot.scatter(ax=ax, x=df.columns[0], y=df.columns[1])
    elif chart_type == 'histogram':
        df.plot.hist(ax=ax)
    
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    
    return fig