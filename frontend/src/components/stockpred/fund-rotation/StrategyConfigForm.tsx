/** Phase 5 Task 2 — render strategy config from Catalog JSON Schema (§18). */

import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";

export interface SchemaField {
  name: string;
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
  disabled?: boolean;
}

function extractFields(
  schema: Record<string, unknown>,
  defaults: Record<string, unknown>,
  descriptions: Record<string, string>,
): { fields: SchemaField[]; unsupported: string[] } {
  const props = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const required = new Set(schema.required as string[] ?? []);
  const fields: SchemaField[] = [];
  const unsupported: string[] = [];

  for (const [name, prop] of Object.entries(props)) {
    const type = prop.type as string;
    if (!["string", "number", "integer", "boolean"].includes(type)) {
      if (type === "object" && prop.properties) {
        // Flatten nested objects: field name = parent_child
        const nested = prop.properties as Record<string, Record<string, unknown>>;
        for (const [nestedName, nestedProp] of Object.entries(nested)) {
          const nestedType = nestedProp.type as string;
          if (!["string", "number", "integer", "boolean"].includes(nestedType)) {
            unsupported.push(`${name}.${nestedName} (${nestedType})`);
            continue;
          }
          const nestedDefaults = (defaults[name] ?? {}) as Record<string, unknown>;
          fields.push({
            name: `${name}_${nestedName}`,
            type: nestedType as SchemaField["type"],
            description:
              descriptions[`${name}_${nestedName}`] ||
              (nestedProp.description as string) ||
              `${name} - ${nestedName}`,
            default: nestedDefaults[nestedName],
            minimum: nestedProp.minimum as number | undefined,
            maximum: nestedProp.maximum as number | undefined,
            enum: nestedProp.enum as (string | number)[] | undefined,
            required: false,
          });
        }
        continue;
      }
      unsupported.push(`${name} (${type})`);
      continue;
    }

    fields.push({
      name,
      type: type as SchemaField["type"],
      description: descriptions[name] || (prop.description as string) || name,
      default: defaults[name],
      minimum: prop.minimum as number | undefined,
      maximum: prop.maximum as number | undefined,
      enum: prop.enum as (string | number)[] | undefined,
      required: required.has(name),
    });
  }

  return { fields, unsupported };
}

export function StrategyConfigForm({
  schema,
  defaults,
  descriptions,
  value,
  onChange,
  disabled = false,
}: Props) {
  const { fields, unsupported } = useMemo(
    () => extractFields(schema, defaults, descriptions),
    [schema, defaults, descriptions],
  );

  const setField = (name: string, val: unknown) => {
    const next = { ...value };
    if (name.includes("_") && !(name in (schema.properties as Record<string, unknown> ?? {}))) {
      // Nested field: parent_child → parent: { child: val }
      const [parent, child] = name.split("_", 2);
      next[parent] = { ...((next[parent] ?? {}) as Record<string, unknown>), [child]: val };
    } else {
      if (val === "" || val === undefined) {
        delete next[name];
      } else {
        next[name] = val;
      }
    }
    onChange(next);
  };

  const getFieldValue = (name: string): unknown => {
    const direct = value[name];
    if (direct !== undefined) return direct;
    const field = fields.find((f) => f.name === name);
    return field?.default;
  };

  const renderInput = (field: SchemaField) => {
    const val = getFieldValue(field.name);
    const baseClass =
      "w-full rounded border px-2 py-1.5 text-sm disabled:opacity-50 disabled:cursor-not-allowed";

    if (field.enum) {
      return (
        <select
          value={String(val ?? "")}
          onChange={(e) => setField(field.name, e.target.value)}
          disabled={disabled}
          className={baseClass}
        >
          <option value="">--</option>
          {field.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
      );
    }

    if (field.type === "boolean") {
      return (
        <select
          value={val === true ? "true" : val === false ? "false" : ""}
          onChange={(e) => {
            const v = e.target.value;
            if (v === "true") setField(field.name, true);
            else if (v === "false") setField(field.name, false);
            else setField(field.name, undefined);
          }}
          disabled={disabled}
          className={baseClass}
        >
          <option value="">默认</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      );
    }

    const step = field.type === "integer" ? 1 : undefined;
    return (
      <input
        type="number"
        value={val !== undefined ? String(val) : ""}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            setField(field.name, undefined);
            return;
          }
          const num = field.type === "integer" ? parseInt(raw, 10) : parseFloat(raw);
          if (!isNaN(num)) setField(field.name, num);
        }}
        min={field.minimum}
        max={field.maximum}
        step={step}
        disabled={disabled}
        placeholder={field.default !== undefined ? String(field.default) : ""}
        className={baseClass}
      />
    );
  };

  return (
    <div className="space-y-3">
      {fields.map((field) => (
        <label
          key={field.name}
          className="flex flex-col gap-1"
        >
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            {field.description}
            {field.required && <span className="text-red-400">*</span>}
          </span>
          {renderInput(field)}
        </label>
      ))}
      {unsupported.length > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 flex items-start gap-2">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            暂不支持以下配置项：{unsupported.join("、")}
          </span>
        </div>
      )}
    </div>
  );
}
