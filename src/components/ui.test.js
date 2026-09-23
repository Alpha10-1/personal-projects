/**
 * The shared primitives.
 *
 * Every other component is built out of these, so a fault here is a fault
 * everywhere at once, and the two that matter most are not visual:
 * `Field` wires a label to the control it describes, and `ErrorNote` is how
 * every failure in the app reaches a person.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  ProgressBar,
  Select,
  Spinner,
} from "@/components/ui";

describe("Field", () => {
  it("wires the label to the control, so clicking it focuses", async () => {
    render(
      <Field label="Test command">
        <input />
      </Field>,
    );
    const input = screen.getByLabelText("Test command");
    await userEvent.click(screen.getByText("Test command"));
    expect(input).toHaveFocus();
  });

  it("keeps an id the caller gave rather than overwriting it", () => {
    render(
      <Field label="Named">
        <input id="mine" />
      </Field>,
    );
    expect(screen.getByLabelText("Named")).toHaveAttribute("id", "mine");
  });

  it("points the control at its hint, so a screen reader reads it too", () => {
    render(
      <Field label="Repo" hint="owner/name">
        <input />
      </Field>,
    );
    const input = screen.getByLabelText("Repo");
    const described = input.getAttribute("aria-describedby");
    expect(described).toBeTruthy();
    expect(document.getElementById(described)).toHaveTextContent("owner/name");
  });

  it("adds no description when there is no hint", () => {
    render(
      <Field label="Bare">
        <input />
      </Field>,
    );
    expect(screen.getByLabelText("Bare")).not.toHaveAttribute("aria-describedby");
  });
});

describe("ErrorNote", () => {
  it("shows nothing when there is no error", () => {
    const { container } = render(<ErrorNote error={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the message from an Error", () => {
    render(<ErrorNote error={new Error("Rate limited by the API.")} />);
    expect(screen.getByText(/Rate limited/)).toBeInTheDocument();
  });

  it("shows a plain string too", () => {
    render(<ErrorNote error="Something went wrong." />);
    expect(screen.getByText(/Something went wrong/)).toBeInTheDocument();
  });

  it("can be dismissed, but only when the caller offers it", async () => {
    const onDismiss = vi.fn();
    const { rerender } = render(<ErrorNote error="Nope." />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();

    rerender(<ErrorNote error="Nope." onDismiss={onDismiss} />);
    await userEvent.click(screen.getByRole("button"));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("Button", () => {
  it("does what it was asked", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalled();
  });

  it("cannot be pressed twice while it is busy", async () => {
    const onClick = vi.fn();
    render(
      <Button busy onClick={onClick}>
        Go
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("stays disabled when told, busy or not", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Go
      </Button>,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("defaults to type=button, so it cannot submit a form by accident", () => {
    render(<Button>Go</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });
});

describe("Select", () => {
  const OPTIONS = [
    { value: "todo", label: "To do" },
    { value: "done", label: "Done" },
  ];

  it("renders the options it was given", () => {
    render(<Select options={OPTIONS} onChange={vi.fn()} value="todo" />);
    expect(screen.getByRole("option", { name: "To do" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Done" })).toBeInTheDocument();
  });

  it("offers a blank only when asked, and labels it", () => {
    const { rerender } = render(
      <Select options={OPTIONS} onChange={vi.fn()} value="todo" />,
    );
    expect(screen.getAllByRole("option")).toHaveLength(2);

    rerender(
      <Select options={OPTIONS} includeBlank blankLabel="Any" onChange={vi.fn()} value="" />,
    );
    expect(screen.getByRole("option", { name: "Any" })).toBeInTheDocument();
  });

  it("reports what was chosen", async () => {
    const onChange = vi.fn();
    render(<Select options={OPTIONS} onChange={onChange} value="todo" />);
    await userEvent.selectOptions(screen.getByRole("combobox"), "done");
    expect(onChange).toHaveBeenCalled();
  });
});

describe("Modal", () => {
  it("is not in the document until it is open", () => {
    const { container } = render(
      <Modal open={false} title="Edit" onClose={vi.fn()}>
        inside
      </Modal>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows its title and contents when open", () => {
    render(
      <Modal open title="Edit project" onClose={vi.fn()}>
        inside
      </Modal>,
    );
    expect(screen.getByText("Edit project")).toBeInTheDocument();
    expect(screen.getByText("inside")).toBeInTheDocument();
  });

  it("closes on Escape, which is the one people reach for", async () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Edit" onClose={onClose}>
        inside
      </Modal>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("the small ones", () => {
  it("Badge shows its text", () => {
    render(<Badge tone="critical">pending</Badge>);
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("CardHeader shows a title, a subtitle and an action", () => {
    render(
      <CardHeader title="Changes" subtitle="3 waiting" action={<button>Add</button>} />,
    );
    expect(screen.getByText("Changes")).toBeInTheDocument();
    expect(screen.getByText("3 waiting")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeInTheDocument();
  });

  it("Card renders what it wraps", () => {
    render(<Card>held</Card>);
    expect(screen.getByText("held")).toBeInTheDocument();
  });

  it("Spinner says what it is waiting for", () => {
    render(<Spinner label="Reading the history" />);
    expect(screen.getByText("Reading the history")).toBeInTheDocument();
  });

  it("EmptyState explains rather than just being empty", () => {
    render(<EmptyState title="Nothing yet" description="Add the first one." />);
    expect(screen.getByText("Nothing yet")).toBeInTheDocument();
    expect(screen.getByText("Add the first one.")).toBeInTheDocument();
  });

  it("ProgressBar clamps rather than overflowing its track", () => {
    const { rerender } = render(<ProgressBar value={150} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");

    rerender(<ProgressBar value={-20} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");

    rerender(<ProgressBar value={undefined} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "0");
  });
});
