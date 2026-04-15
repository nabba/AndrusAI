import matplotlib.pyplot as plt
import pandas as pd

def create_visualization(data, chart_type='bar'):
    """
    Create visualizations from data
    Supported types: bar, line, scatter, pie
    """
    df = pd.DataFrame(data)
    
    if chart_type == 'bar':
        df.plot.bar()
    elif chart_type == 'line':
        df.plot.line()
    elif chart_type == 'scatter':
        df.plot.scatter()
    elif chart_type == 'pie':
        df.plot.pie()
    
    plt.savefig('output.png')
    return 'output.png'