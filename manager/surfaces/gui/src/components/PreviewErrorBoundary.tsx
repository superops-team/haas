import { Component, type ErrorInfo, type ReactNode } from "react";

export class PreviewErrorBoundary extends Component<
  { children: ReactNode; fallback: ReactNode; resetToken: unknown },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Preview content and paths are deliberately not logged from this security-sensitive surface.
  }

  componentDidUpdate(previous: Readonly<{ resetToken: unknown }>) {
    if (this.state.failed && previous.resetToken !== this.props.resetToken) {
      this.setState({ failed: false });
    }
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
