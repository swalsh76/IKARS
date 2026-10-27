// The generated validators, by contract name (src/api/validators.js is made by scripts/gen-contracts.mjs).

import type { ContractName } from "./contracts";
import * as generated from "./validators.js";
import type { Validator } from "./validators.js";

export const validators = generated as unknown as Record<ContractName, Validator>;
