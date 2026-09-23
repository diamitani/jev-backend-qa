import { experimental_evaluate as evaluate } from 'ai';

const readStdin = () => new Promise((resolve) => {
  let data = '';
  process.stdin.on('data', (c) => { data += c; });
  process.stdin.on('end', () => resolve(data));
});

const input = JSON.parse(await readStdin());
const started = Date.now();
try {
  const result = await evaluate({
    model: input.model || 'typesafe-ai/jev',
    apiKey: process.env.AI_GATEWAY_API_KEY,
    baseURL: process.env.AI_GATEWAY_BASE_URL || 'https://ai-gateway.vercel.sh/v1',
    state: input.state,
    questions: input.questions,
  });
  console.log(JSON.stringify({ ok: true, ms: Date.now() - started, result }));
} catch (e) {
  console.log(JSON.stringify({ ok: false, ms: Date.now() - started, error: String((e && e.message) || e) }));
}
