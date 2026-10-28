import { InputHTMLAttributes, forwardRef, SelectHTMLAttributes } from "react";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  ({ className = "", ...props }, ref) => (
    <input
      ref={ref}
      className={`focus-ring h-9 w-full rounded-md border border-border-strong bg-white px-3 text-sm text-ink placeholder:text-ink-faint disabled:bg-neutral-50 disabled:text-ink-faint ${className}`}
      {...props}
    />
  ),
);
Input.displayName = "Input";

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options: SelectOption[];
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ options, placeholder, className = "", ...props }, ref) => (
    <select
      ref={ref}
      className={`focus-ring h-9 w-full rounded-md border border-border-strong bg-white px-2.5 text-sm text-ink ${className}`}
      {...props}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  ),
);
Select.displayName = "Select";
