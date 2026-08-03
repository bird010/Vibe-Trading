/** Render a strategy config from the Catalog JSON Schema. */

import { useEffect, useMemo, useRef } from "react";
import { AlertTriangle } from "lucide-react";

export interface SchemaField {
  key: string;
  path: string[];
  type: "string" | "number" | "integer" | "boolean";
  description: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  enum?: (string | number)[];
  required: boolean;
}

interface Props {
  schema: Record<string, unknown>;
  defaults: Record<string, unknown>;
  descriptions: Record<string, string>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  onUnsupportedChange?: (unsupported: string[]) => void;
  disabled?: boolean;
}

const SUPPORTED_TYPES = new Set(["string", "number", "integer", "boolean"]);

function extractFields(
  schema: Record<string, unknown>,
  defaults: Record<string, unknown>,
  descriptions: Record<string, string>,
): { fields: SchemaField[]; unsupported: string[] } {
  const properties = (schema.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = new Set((schema.required as string[] | undefined) ?? []);
  const fields: SchemaField[] = [];
  const unsupported: string[] = [];

  const pushField = (
    path: string[],
    property: Record<string, unknown>,
    fieldDefault: unknown,
    isRequired: boolean,
  ): void => {
    const type = property.type as string;
    const dotted = path.join(".");
    if (!SUPPORTED_TYPES.has(type)) {
      unsupported.push(`${dotted} (${type || "unknown"})`);
      return;
    }
    fields.push({
      key: dotted,
      path,
      type: type as SchemaField["type"],
      description:
        descriptions[dotted] ||
        descriptions[path.join("_")] ||
        (property.description as string) ||
        dotted,
      default: fieldDefault,
      minimum: property.minimum as number | undefined,
      maximum: property.maximum as number | undefined,
      enum: property.enum as (string | number)[] | undefined,
      required: isRequired,
    });
  };

  for (const [name, property] of Object.entries(properties)) {
    if (property.type === "object" && property.properties) {
      const nestedProperties = property.properties as Record<
        string,
        Record<string, unknown>
      >;
      const nestedDefaults = (defaults[name] ?? {}) as Record<string, unknown>;
      const nestedRequired = new Set(
        (property.required as string[] | undefined) ?? [],
      );
      for (const [nestedName, nestedProperty] of Object.entries(
        nestedProperties,
      )) {
        pushField(
          [name, nestedName],
          nestedProperty,
          nestedDefaults[nestedName],
          required.has(name) && nestedRequired.has(nestedName),
        );
      }
      continue;
    }
    pushField([name], property, defaults[name], required.has(name));
  }
  return { fields, unsupported };
}

function getAtPath(
  source: Record<string, unknown>,
  path: string[],
): unknown {
  let current: unknown = source;
  for (const segment of path) {
    if (!current || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[segment];
  }
  return current;
}

function setAtPath(
  source: Record<string, unknown>,
  path: string[],
  value: unknown,
): Record<string, unknown> {
  const result = structuredClone(source);
  if (path.length === 1) {
    if (value === undefined || value === "") delete result[path[0]];
    else result[path[0]] = value;
    return result;
  }
  const [parent, child] = path;
  const nested = {
    ...((result[parent] ?? {}) as Record<string, unknown>),
  };
  if (value === undefined || value === "") delete nested[child];
  else nested[child] = value;
  result[parent] = nested;
  return result;
}

function castEnumValue(field: SchemaField, raw: string): unknown {
  if (raw === "") return undefined;
  const option = field.enum?.find((candidate) => String(candidate) === raw);
  return option ?? raw;
}

export function StrategyConfigForm({
  schema,
  defaults,
  descriptions,
  value,
  onChange,
  onUnsupportedChange,
  disabled = false,
}: Props) {
  const { fields, unsupported } = useMemo(
    () => extractFields(schema, defaults, descriptions),
    [schema, defaults, descriptions],
  );
  const unsupportedSignature = unsupported.join("\u0000");
  const unsupportedCallbackRef = useRef(onUnsupportedChange);

  useEffect(() => {
    unsupportedCallbackRef.current = onUnsupportedChange;
  }, [onUnsupportedChange]);

  useEffect(() => {
    unsupportedCallbackRef.current?.(unsupported);
    // Callback identity must not retrigger parent updates; only a changed
    // unsupported-field set is semantically relevant.
  }, [unsupportedSignature]);

  const fieldValue = (field: SchemaField): unknown => {
    const current = getAtPath(value, field.path);
    return current !== undefined ? current : field.default;
  };

  const setField = (field: SchemaField, nextValue: unknown): void => {
    onChange(setAtPath(value, field.path, nextValue));
  };

  const renderInput = (field: SchemaField) => {
    const current = fieldValue(field);
    const className =
      "w-full rounded border px-2 py-1.5 text-sm disabled:opacity-50 disabled:cursor-not-allowed";

    if (field.enum) {
      return (
        <select
          value={String(current ?? "")}
          onChange={(event) =>
            setField(field, castEnumValue(field, event.target.value))
          }
          disabled={disabled}
          className={className}
        >
          <option value="">默认</option>
          {field.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      );
    }

    if (field.type === "boolean") {
      return (
        <select
          value={current === true ? "true" : current === false ? "false" : ""}
          onChange={(event) => {
            if (event.target.value === "true") setField(field, true);
            else if (event.target.value === "false") setField(field, false);
            else setField(field, undefined);
          }}
          disabled={disabled}
          className={className}
        >
          <option value="">默认</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      );
    }

    if (field.type === "string") {
      return (
        <input
          type="text"
          value={current !== undefined ? String(current) : ""}
          onChange={(event) => setField(field, event.target.value)}
          disabled={disabled}
          placeholder={field.default !== undefined ? String(field.default) : ""}
          className={className}
        />
      );
    }

    return (
      <input
        type="number"
        value={current !== undefined ? String(current) : ""}
        onChange={(event) => {
          const raw = event.target.value;
          if (raw === "") {
            setField(field, undefined);
            return;
          }
          const parsed =
            field.type === "integer" ? Number.parseInt(raw, 10) : Number(raw);
          if (Number.isFinite(parsed)) setField(field, parsed);
        }}
        min={field.minimum}
        max={field.maximum}
        step={field.type === "integer" ? 1 : "any"}
        disabled={disabled}
        placeholder={field.default !== undefined ? String(field.default) : ""}
        className={className}
      />
    );
  };

  return (
    <div className="space-y-3">
      {fields.map((field) => (
        <label key={field.key} className="flex flex-col gap-1">
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            {field.description}
            {field.required && <span className="text-red-400">*</span>}
          </span>
          {renderInput(field)}
        </label>
      ))}
      {unsupported.length > 0 && (
        <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            当前客户端无法安全编辑以下配置项，提交已禁用：
            {unsupported.join("、")}
          </span>
        </div>
      )}
    </div>
  );
}
