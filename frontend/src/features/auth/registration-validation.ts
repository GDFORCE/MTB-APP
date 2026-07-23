export type RegistrationVariant = "sponsor" | "site" | "smo" | "patient";
export type RegistrationFields = Record<string, string>;
export type RegistrationErrors = Partial<Record<string, string>>;

export type RegistrationValidation = {
  valid: boolean;
  errors: RegistrationErrors;
  normalized: RegistrationFields;
  age: number | null;
};

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export function normalizeEmail(value = "") {
  return value.trim().toLowerCase();
}

export function normalizeIndianPhone(value = "") {
  let digits = value.replace(/\D/g, "");
  if (digits.startsWith("0091")) digits = digits.slice(4);
  else if (digits.startsWith("91") && digits.length === 12) digits = digits.slice(2);
  else if (digits.startsWith("0") && digits.length === 11) digits = digits.slice(1);
  return /^[6-9]\d{9}$/.test(digits) ? `+91${digits}` : "";
}

export function parseDob(value = "", reference = new Date()) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(year, month - 1, day);
  if (
    date.getFullYear() !== year
    || date.getMonth() !== month - 1
    || date.getDate() !== day
  ) return null;
  const today = new Date(
    reference.getFullYear(),
    reference.getMonth(),
    reference.getDate(),
  );
  if (date > today) return null;
  let age = today.getFullYear() - date.getFullYear();
  const monthDelta = today.getMonth() - date.getMonth();
  if (monthDelta < 0 || (monthDelta === 0 && today.getDate() < date.getDate())) age--;
  if (age < 0 || age > 120) return null;
  return { date, age, canonical: `${match[1]}-${match[2]}-${match[3]}` };
}

function required(
  fields: RegistrationFields,
  errors: RegistrationErrors,
  key: string,
  label: string,
) {
  if (!fields[key]?.trim()) errors[key] = `${label} is required.`;
}

export function validateRegistration(
  variant: RegistrationVariant,
  fields: RegistrationFields,
  referenceDate = new Date(),
): RegistrationValidation {
  const normalized: RegistrationFields = {};
  for (const [key, value] of Object.entries(fields)) normalized[key] = value?.trim() || "";
  normalized.email = normalizeEmail(fields.email);
  normalized.phone = normalizeIndianPhone(fields.phone);

  const errors: RegistrationErrors = {};
  required(fields, errors, "fullName", "Full name");

  if (!normalized.phone) {
    errors.phone = fields.phone?.trim()
      ? "Enter a valid 10-digit Indian mobile number."
      : "Phone number is required.";
  }

  if (!normalized.email) {
    errors.email = "Email ID is required.";
  } else if (!EMAIL_RE.test(normalized.email)) {
    errors.email = "Enter a valid email address.";
  }

  let age: number | null = null;
  if (variant === "patient") {
    required(fields, errors, "dob", "Date of birth");
    const parsed = parseDob(fields.dob, referenceDate);
    if (fields.dob?.trim() && !parsed) {
      errors.dob = "Enter a real date in YYYY-MM-DD format (age 0–120).";
    } else if (parsed) {
      age = parsed.age;
      normalized.dob = parsed.canonical;
    }
    required(fields, errors, "gender", "Gender");
  } else {
    required(fields, errors, "designation", "Designation");
    required(fields, errors, "orgName", variant === "smo" ? "SMO name" : "Organization name");
    required(fields, errors, "orgAddress", variant === "smo" ? "SMO address" : "Organization address");
    if (variant === "site") {
      required(fields, errors, "hospitalType", "Hospital type");
      required(fields, errors, "role", "Role");
    }
  }

  return {
    valid: Object.keys(errors).length === 0,
    errors,
    normalized,
    age,
  };
}
