import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface in dev; a future enhancement could push to a telemetry endpoint.
    console.error('ErrorBoundary caught:', error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div className="min-h-[40vh] flex items-center justify-center p-6">
        <div className="bg-[#111820] border border-[#f87171]/30 rounded-xl p-6 max-w-lg w-full">
          <h2 className="text-lg font-semibold text-[#f87171] mb-2">Something went wrong</h2>
          <p className="text-sm text-[#7a8599] mb-4">{error.message}</p>
          <button
            onClick={this.reset}
            className="px-4 py-2 bg-[#60a5fa]/20 border border-[#60a5fa]/30 text-[#60a5fa] text-sm rounded-lg hover:bg-[#60a5fa]/30 transition-colors"
          >
            Try again
          </button>
        </div>
      </div>
    );
  }
}
