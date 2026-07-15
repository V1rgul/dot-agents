#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { findSkills, readAllowImplicit, readFrontmatter, readTopLevelField, slash } = require("./lib/skill-metadata-common");

const root = path.resolve(process.argv[2] || "skills");

if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
	console.error(`Skills directory not found: ${root}`);
	process.exit(1);
}

function setAllowImplicit(text, allowImplicit) {
	const newline = text.includes("\r\n") ? "\r\n" : "\n";
	const value = String(allowImplicit);
	const lines = text.split(/\r?\n/);
	const policyIndex = lines.findIndex((line) => /^policy\s*:\s*(?:#.*)?$/.test(line));
	const anyPolicyIndex = lines.findIndex((line) => /^policy\s*:/.test(line));

	if (policyIndex < 0 && anyPolicyIndex >= 0) {
		throw new Error("inline policy mappings are unsupported");
	}

	if (policyIndex < 0) {
		const prefix = text && !text.endsWith(newline) ? `${text}${newline}${newline}` : text;
		const separator = prefix && !prefix.endsWith(`${newline}${newline}`) ? newline : "";
		return `${prefix}${separator}policy:${newline}  allow_implicit_invocation: ${value}${newline}`;
	}

	const policyIndent = lines[policyIndex].match(/^\s*/)[0].length;
	let policyEnd = lines.length;
	for (let index = policyIndex + 1; index < lines.length; index += 1) {
		if (!lines[index].trim() || /^\s*#/.test(lines[index])) continue;
		if (lines[index].match(/^\s*/)[0].length <= policyIndent) {
			policyEnd = index;
			break;
		}
	}

	const fieldIndex = lines.findIndex((line, index) => index > policyIndex && index < policyEnd && /^\s*allow_implicit_invocation\s*:/.test(line));

	if (fieldIndex < 0) {
		lines.splice(policyIndex + 1, 0, `${" ".repeat(policyIndent + 2)}allow_implicit_invocation: ${value}`);
		return lines.join(newline);
	}

	const line = lines[fieldIndex];
	const colon = line.indexOf(":");
	const comment = line.slice(colon + 1).match(/\s+#.*$/)?.[0] || "";
	const currentValue = line.slice(colon + 1, comment ? -comment.length : undefined).trim();
	if (currentValue.toLowerCase() === value) return text;
	lines[fieldIndex] = `${line.slice(0, colon + 1)} ${value}${comment}`;
	return lines.join(newline);
}

let skipped = 0;

for (const skillFile of findSkills(root).sort()) {
	const frontmatter = readFrontmatter(skillFile);
	const rawDisable = readTopLevelField(frontmatter, "disable-model-invocation");
	const identifier = slash(path.relative(root, path.dirname(skillFile))) || readTopLevelField(frontmatter, "name") || ".";

	if (rawDisable !== undefined && !/^(true|false)$/i.test(rawDisable)) {
		console.log(`skipped: ${identifier} (invalid disable-model-invocation value "${rawDisable}")`);
		skipped += 1;
		continue;
	}

	const allowImplicit = rawDisable?.toLowerCase() !== "true";
	const agentsDirectory = path.join(path.dirname(skillFile), "agents");
	const yaml = path.join(agentsDirectory, "openai.yaml");
	const yml = path.join(agentsDirectory, "openai.yml");
	const existingMetadataFile = fs.existsSync(yaml) ? yaml : fs.existsSync(yml) ? yml : undefined;
	if (allowImplicit && readAllowImplicit(existingMetadataFile) === true) {
		console.log(`correct: ${identifier}`);
		continue;
	}
	const metadataFile = existingMetadataFile || yaml;

	try {
		const exists = fs.existsSync(metadataFile);
		const current = exists ? fs.readFileSync(metadataFile, "utf8") : "";
		const updated = setAllowImplicit(current, allowImplicit);
		if (updated === current) {
			console.log(`correct: ${identifier}`);
			continue;
		}

		fs.mkdirSync(path.dirname(metadataFile), { recursive: true });
		fs.writeFileSync(metadataFile, updated, "utf8");
		console.log(`fixed: ${identifier} (${exists ? "~" : "+"}${path.basename(metadataFile)})`);
	} catch (error) {
		console.log(`skipped: ${identifier} (${String(error.message).replace(/\s+/g, " ")})`);
		skipped += 1;
	}
}

if (skipped) process.exitCode = 1;
