"use strict";

const fs = require("node:fs");
const path = require("node:path");

const slash = (value) => value.split(path.sep).join("/");
const compact = (value) => value.replace(/\s+/g, " ").trim();

function parseScalar(raw) {
	const value = raw.trim().replace(/\s+#.*$/, "");
	if (value.startsWith('"') && value.endsWith('"')) {
		try {
			return JSON.parse(value);
		} catch {
			return value.slice(1, -1);
		}
	}
	if (value.startsWith("'") && value.endsWith("'")) {
		return value.slice(1, -1).replace(/''/g, "'");
	}
	return value;
}

function readTopLevelField(document, key) {
	const lines = document.split(/\r?\n/);
	const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
	const pattern = new RegExp(`^${escapedKey}\\s*:\\s*(.*)$`);

	for (let index = 0; index < lines.length; index += 1) {
		const match = lines[index].match(pattern);
		if (!match) continue;

		const raw = match[1].trim();
		if (!/^[>|][+-]?$/.test(raw)) return parseScalar(raw);

		const block = [];
		for (let next = index + 1; next < lines.length; next += 1) {
			if (lines[next] && !/^\s/.test(lines[next])) break;
			block.push(lines[next].trim());
		}
		return raw.startsWith("|") ? block.join("\n").trim() : compact(block.join(" "));
	}
	return undefined;
}

function readFrontmatter(file) {
	const text = fs.readFileSync(file, "utf8").replace(/^\uFEFF/, "");
	const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
	return match ? match[1] : "";
}

function readAllowImplicit(file) {
	if (!file) return true;

	const lines = fs.readFileSync(file, "utf8").split(/\r?\n/);
	let policyIndent;

	for (const line of lines) {
		if (/^\s*#/.test(line) || !line.trim()) continue;
		const indent = line.match(/^\s*/)[0].length;

		if (policyIndent === undefined) {
			if (/^policy\s*:\s*(?:#.*)?$/.test(line)) policyIndent = indent;
			continue;
		}

		if (indent <= policyIndent) break;
		const match = line.match(/^\s*allow_implicit_invocation\s*:\s*(.*)$/);
		if (!match) continue;

		const raw = parseScalar(match[1]).toLowerCase();
		if (raw === "true" || raw === "false") return raw === "true";
		return undefined;
	}

	return true;
}

function findSkills(directory, found = [], visited = new Set()) {
	const realDirectory = fs.realpathSync(directory);
	if (visited.has(realDirectory)) return found;
	visited.add(realDirectory);

	for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
		const fullPath = path.join(directory, entry.name);
		let stats;
		if (entry.isSymbolicLink()) {
			try {
				stats = fs.statSync(fullPath);
			} catch {
				continue;
			}
		}

		if (entry.isDirectory() || stats?.isDirectory()) {
			findSkills(fullPath, found, visited);
		} else if (entry.name === "SKILL.md" && (entry.isFile() || stats?.isFile())) {
			found.push(fullPath);
		}
	}
	return found;
}

module.exports = {
	compact,
	findSkills,
	readAllowImplicit,
	readFrontmatter,
	readTopLevelField,
	slash,
};
