"use client";

import { useEffect, useState, type FormEvent } from "react";

import type { RepositoryCreatePayload } from "@/types/repositories";

interface ImportRepositoryDialogProps {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onSubmit: (payload: RepositoryCreatePayload) => void | Promise<void>;
}

interface FormValues {
  name: string;
  repoUrl: string;
  branch: string;
}

type FormErrors = Partial<Record<keyof FormValues, string>>;

const emptyValues: FormValues = {
  name: "",
  repoUrl: "",
  branch: "main",
};

function validate(values: FormValues): FormErrors {
  const errors: FormErrors = {};
  if (!values.name.trim()) {
    errors.name = "Give this repository a display name.";
  } else if (values.name.trim().length > 255) {
    errors.name = "Repository names must be 255 characters or fewer.";
  }

  const githubUrl = /^https:\/\/github\.com\/[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,104}(?:\.git)?$/u;
  if (!githubUrl.test(values.repoUrl.trim())) {
    errors.repoUrl = "Use a GitHub HTTPS URL, for example https://github.com/acme/store.";
  }

  const branch = values.branch.trim();
  if (!branch || branch.length > 128 || branch.startsWith("-") || branch.startsWith("refs/") || branch.includes("@{")) {
    errors.branch = "Enter a valid Git branch name.";
  }
  return errors;
}

export function ImportRepositoryDialog({
  open,
  pending,
  onClose,
  onSubmit,
}: ImportRepositoryDialogProps) {
  const [values, setValues] = useState<FormValues>(emptyValues);
  const [errors, setErrors] = useState<FormErrors>({});

  useEffect(() => {
    if (open) {
      setValues(emptyValues);
      setErrors({});
    }
  }, [open]);

  useEffect(() => {
    if (!open) {
      return;
    }
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape" && !pending) {
        onClose();
      }
    }
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, open, pending]);

  if (!open) {
    return null;
  }

  function updateValue(field: keyof FormValues, value: string): void {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const nextErrors = validate(values);
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    await onSubmit({
      name: values.name.trim(),
      repo_url: values.repoUrl.trim(),
      default_branch: values.branch.trim(),
      use_sample_monolith: false,
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/70 px-5 py-8"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) {
          onClose();
        }
      }}
    >
      <section
        aria-labelledby="import-repository-title"
        aria-modal="true"
        className="w-full max-w-lg rounded-lg border border-hairline bg-panel p-6 shadow-2xl shadow-black/40"
        role="dialog"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-indigo-300">
              Repository intake
            </p>
            <h2 id="import-repository-title" className="mt-2 text-lg font-semibold">
              Import GitHub Repo
            </h2>
          </div>
          <button
            type="button"
            aria-label="Close import dialog"
            disabled={pending}
            onClick={onClose}
            className="rounded-md px-2 py-1 text-xl leading-none text-secondaryText hover:bg-surface hover:text-primaryText disabled:cursor-not-allowed disabled:opacity-50"
          >
            ×
          </button>
        </div>

        <form className="mt-6 space-y-4" onSubmit={submit} noValidate>
          <Field
            id="repository-name"
            label="Display name"
            value={values.name}
            error={errors.name}
            onChange={(value) => updateValue("name", value)}
            placeholder="Payments service"
          />
          <Field
            id="repository-url"
            label="GitHub HTTPS URL"
            value={values.repoUrl}
            error={errors.repoUrl}
            onChange={(value) => updateValue("repoUrl", value)}
            placeholder="https://github.com/acme/store"
            inputMode="url"
          />
          <Field
            id="repository-branch"
            label="Default branch"
            value={values.branch}
            error={errors.branch}
            onChange={(value) => updateValue("branch", value)}
            placeholder="main"
          />

          <div className="flex flex-wrap justify-end gap-3 border-t border-hairline pt-5">
            <button
              type="button"
              disabled={pending}
              onClick={onClose}
              className="rounded-md border border-hairline px-4 py-2.5 text-sm text-secondaryText hover:bg-surface hover:text-primaryText disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={pending}
              className="rounded-md bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:cursor-wait disabled:opacity-60"
            >
              {pending ? "Starting ingestion…" : "Import repository"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

interface FieldProps {
  id: string;
  label: string;
  value: string;
  error?: string;
  placeholder: string;
  inputMode?: "url";
  onChange: (value: string) => void;
}

function Field({ id, label, value, error, placeholder, inputMode, onChange }: FieldProps) {
  return (
    <div>
      <label htmlFor={id} className="mb-2 block font-mono text-xs text-secondaryText">
        {label}
      </label>
      <input
        id={id}
        name={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        inputMode={inputMode}
        aria-describedby={error ? `${id}-error` : undefined}
        aria-invalid={Boolean(error)}
        className={`w-full rounded-md border bg-surface px-3 py-2.5 text-sm text-primaryText placeholder:text-mutedText ${
          error ? "border-red-500/70" : "border-hairline"
        }`}
      />
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-xs text-red-400">
          {error}
        </p>
      ) : null}
    </div>
  );
}
