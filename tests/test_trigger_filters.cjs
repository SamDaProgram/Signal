const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(
  path.join(__dirname, "../static/triggers.js"),
  "utf8",
);
const context = vm.createContext({});
vm.runInContext(
  source.slice(
    source.indexOf("function triggerMatches("),
    source.indexOf("function applyTriggerFilters("),
  ),
  context,
);
const matches = context.triggerMatches;
const rule = {
  name: "Start Preshow",
  enabled: true,
  universe: 0,
  channel: 501,
  address: "/preshow",
  host: "127.0.0.1",
};

test("trigger search combines words across name, channel, group and destination", () => {
  const labels = { "0:501": "Front yard" };
  assert.equal(matches(rule, "  START  501 ", "all", "all", labels), true);
  assert.equal(matches(rule, "yard /preshow", "all", "osc", labels), true);
  assert.equal(matches(rule, "127.0.0.1 0:501", "all", "all", labels), true);
  assert.equal(matches(rule, "missing", "all", "all", labels), false);
});
test("status and action filters combine and support legacy OSC records", () => {
  assert.equal(matches(rule, "", "enabled", "osc"), true);
  assert.equal(matches(rule, "", "disabled", "osc"), false);
  assert.equal(matches(rule, "", "enabled", "sequence"), false);
  assert.equal(
    matches(
      { ...rule, enabled: false, action: "sequence" },
      "start",
      "disabled",
      "sequence",
    ),
    true,
  );
});
test("view filtering leaves saved priority and trigger configuration untouched", () => {
  const rules = [rule, { ...rule, name: "Other", channel: 20 }];
  const original = JSON.stringify(rules);
  assert.deepEqual(
    rules.filter((r) => matches(r, "501", "all", "all")),
    [rule],
  );
  assert.equal(JSON.stringify(rules), original);
});
