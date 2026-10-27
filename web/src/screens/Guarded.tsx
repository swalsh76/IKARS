import { Component, type ErrorInfo, type ReactNode } from "react";

/** A part of a screen that cannot be drawn (an odd value, a bug) says so in its own place; the rest
 * of the terminal keeps working (M1.8a-web-1b merge review 3). */
export class Guarded extends Component<{ title: string; children: ReactNode }, { error: string | null }> {
  override state = { error: null as string | null };

  static getDerivedStateFromError(error: unknown): { error: string } {
    return { error: error instanceof Error ? error.message : "an unknown error" };
  }

  override componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(`IKARS: ${this.props.title} could not be drawn`, error, info.componentStack);
  }

  override render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    return (
      <section className="card" aria-label={this.props.title}>
        <h2>{this.props.title}</h2>
        <p className="muted small">This could not be drawn: {this.state.error}</p>
      </section>
    );
  }
}
