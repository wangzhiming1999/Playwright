import { Component, type ReactNode } from 'react';
import './ErrorBoundary.css';

interface Props { children: ReactNode; }
interface State { hasError: boolean; error: Error | null; }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  handleReset = () => this.setState({ hasError: false, error: null });

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary">
          <div className="error-boundary-icon">⚠</div>
          <h2>页面渲染出错</h2>
          <p className="error-boundary-msg">{this.state.error?.message}</p>
          <button className="btn-primary" onClick={this.handleReset}>重试</button>
        </div>
      );
    }
    return this.props.children;
  }
}
