"use client";

import { FormEvent, useState } from "react";

export function SearchBar({
  onSearch,
  onClear,
  initialValue = "",
}: {
  onSearch: (query: string) => void;
  onClear: () => void;
  initialValue?: string;
}) {
  const [value, setValue] = useState(initialValue);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = value.trim();
    if (trimmed) onSearch(trimmed);
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="Type a question or keyword…"
        className="flex-1 rounded-md border border-border bg-surface px-3.5 py-2.5 text-sm text-ink
                   focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-colors"
      />
      <button
        type="submit"
        className="bg-accent hover:bg-accent-hover text-white text-sm font-medium rounded-md px-5 transition-colors"
      >
        Search
      </button>
      <button
        type="button"
        onClick={() => {
          setValue("");
          onClear();
        }}
        className="border border-border hover:bg-canvas text-ink-muted text-sm font-medium rounded-md px-4 transition-colors"
      >
        Clear
      </button>
    </form>
  );
}
