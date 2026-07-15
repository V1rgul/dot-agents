#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { compact, findSkills, readAllowImplicit, readFrontmatter, readTopLevelField, slash } = require("./lib/skill-metadata-common");

function main(skillsDirectory = process.argv[2] || "skills") {
	const root = path.resolve(skillsDirectory);
	if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
		console.error(`Skills directory not found: ${root}`);
		process.exitCode = 1;
		return;
	}

	const warnings = [];
	const skills = findSkills(root)
		.map((file) => {
			const frontmatter = readFrontmatter(file);
			const relativePath = slash(path.relative(root, file));
			const identifier = slash(path.relative(root, path.dirname(file))) || readTopLevelField(frontmatter, "name");
			const rawDisable = readTopLevelField(frontmatter, "disable-model-invocation");
			let disabled = false;

			if (rawDisable !== undefined) {
				if (/^(true|false)$/i.test(rawDisable)) disabled = rawDisable.toLowerCase() === "true";
				else warnings.push(`${relativePath}: invalid disable-model-invocation value "${rawDisable}"`);
			}

			const directory = path.dirname(file);
			const yaml = path.join(directory, "agents", "openai.yaml");
			const yml = path.join(directory, "agents", "openai.yml");
			const openaiFile = fs.existsSync(yaml) ? yaml : fs.existsSync(yml) ? yml : undefined;
			const implicit = readAllowImplicit(openaiFile);

			return {
				path: identifier,
				description: compact(readTopLevelField(frontmatter, "description") || "(missing)"),
				disabled,
				disableState: rawDisable === undefined ? "missing (default false)" : rawDisable,
				implicit,
			};
		})
		.sort((left, right) => left.path.localeCompare(right.path));

	for (const disabled of [true, false]) {
		const group = skills.filter((skill) => skill.disabled === disabled);
		const label = disabled ? "Non-model-invocable skills" : "Model-invocable skills";
		console.log(`${label} (${group.length})`);
		if (!group.length) console.log("- (none)");
		for (const skill of group) {
			console.log(`- ${skill.path}: ${skill.description}`);
		}
		console.log();
	}

	const mismatches = skills.filter((skill) => skill.implicit === undefined || skill.implicit !== !skill.disabled);

	console.log(`\nInvocation metadata mismatches (${mismatches.length})`);
	if (!mismatches.length) console.log("- (none)");
	for (const skill of mismatches) {
		console.log(`- ${skill.path}: frontmatter disable-model-invocation: ${skill.disableState}, ` + `openai.yaml allow_implicit_invocation: ${String(skill.implicit)}`);
	}

	if (warnings.length) {
		console.log(`\nWarnings (${warnings.length})`);
		for (const warning of warnings) console.log(`- ${warning}`);
	}
}

main();
