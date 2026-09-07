// Appliance dispatcher: Auth validates user tokens; workers get no privileged keys.
// No remote imports: usable on the internal-only network with an empty cache.
const authUrl = Deno.env.get('SUPABASE_AUTH_URL')!;
const allowed = new Set<string>(); // Populate only after reviewing each function.
Deno.serve(async (request: Request) => {
  const name = new URL(request.url).pathname.split('/')[1];
  if (!name || !/^[a-z][a-z0-9-]*$/.test(name) || !allowed.has(name)) {
    return new Response('Not found', { status: 404 });
  }
  const authorization = request.headers.get('Authorization') ?? '';
  if (!/^Bearer [A-Za-z0-9_.-]+$/.test(authorization)) {
    return new Response('Unauthorized', { status: 401 });
  }
  try {
    const check = await fetch(`${authUrl}/user`, {
      headers: { Authorization: authorization },
      signal: AbortSignal.timeout(5000),
    });
    if (!check.ok) return new Response('Unauthorized', { status: 401 });
    const user = await check.json();
    if (!user.id || user.is_anonymous) return new Response('Unauthorized', { status: 401 });
    const worker = await EdgeRuntime.userWorkers.create({
      servicePath: `/home/deno/functions/${name}`,
      memoryLimitMb: 128,
      workerTimeoutMs: 30000,
      noModuleCache: false,
      importMapPath: null,
      envVars: [
        ['SUPABASE_URL', Deno.env.get('SUPABASE_URL')!],
        ['SUPABASE_ANON_KEY', Deno.env.get('SUPABASE_ANON_KEY')!],
      ],
    });
    // Function must forward the caller JWT for RLS and authorize the operation.
    return await worker.fetch(request);
  } catch {
    return new Response('Service unavailable', { status: 503 });
  }
});
