import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

// The injection bans (PLAN D11, owner 2026-10-27): outside text (market titles, later news and LLM
// output) is drawn only as text, never as markup, and no code is made from strings.
const injectionBans = [
  { selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']", message: "Outside text is drawn as text, never as markup." },
  { selector: "MemberExpression[property.name=/^(innerHTML|outerHTML)$/]", message: "Outside text is drawn as text, never as markup." },
  { selector: "CallExpression[callee.property.name=/^(insertAdjacentHTML|write|writeln)$/]", message: "No markup from strings." },
  { selector: "NewExpression[callee.name='Function']", message: "No code from strings." },
  { selector: "CallExpression[callee.name=/^(setTimeout|setInterval)$/][arguments.0.type='Literal']", message: "No code from strings." },
];

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results", "src/api/contracts.ts", "src/api/validators.*"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-script-url": "error",
      "no-restricted-syntax": ["error", ...injectionBans],
    },
  },
  { files: ["scripts/**", "*.config.*", "e2e/**"], languageOptions: { globals: { ...globals.node } } },
);
