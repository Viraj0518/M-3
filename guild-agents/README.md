# Guild.ai agent prototypes

These are small, credential-free Guild.ai prototypes for the two PALIMPSEST
specialists:

- `extraction-agent/agent.ts` turns a conversation into structured memory candidates.
- `synthesis-agent/agent.ts` answers a question using extracted evidence and abstains when evidence is insufficient.

The SVGs in `diagrams/` show the agents and their handoff.

## Try them in Guild

Guild's CLI requires Node.js 18+ and a Guild account:

```bash
npm install -g @guildai/cli
guild auth login

mkdir -p /tmp/memmotion-extraction-agent
cd /tmp/memmotion-extraction-agent
guild agent init --name memmotion-extraction --template LLM
cp /path/to/M-3/guild-agents/extraction-agent/agent.ts agent.ts
guild agent test --ephemeral
```

Repeat with `synthesis-agent/agent.ts`. The `--ephemeral` flag keeps the first
test out of Guild version history. Save and publish only after the prompts look
right.

## Example handoff

The extraction agent produces claims with evidence, timestamps, and confidence.
The synthesis agent consumes those claims with a question and returns an answer,
citations, confidence, and a review flag.
