// Pinned pi extension: a single read-only replay tool and frozen Skill resources.
import { appendFileSync, readFileSync, realpathSync } from 'node:fs';
import { relative, resolve, isAbsolute } from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { Type } from 'typebox';
import { defineTool, type ExtensionAPI } from '@earendil-works/pi-coding-agent';

export default function (pi: ExtensionAPI) {
  const planPath = process.env.CERNORA_SKILL_PLAN!;
  const plan = JSON.parse(readFileSync(planPath, 'utf8'));
  const skillRoot = realpathSync(process.env.CERNORA_SKILL_ROOT!);
  const out = process.env.CERNORA_CAPTURE_OUT!;
  let requests = 0, calls = 0, hook = 0, usedHook = 0, currentRequest = 0;
  let waiting = false;
  const log = (file: string, row: unknown) => appendFileSync(resolve(out, file), JSON.stringify(row)+'\n');
  const sha = (text: string) => createHash('sha256').update(text).digest('hex');
  const stop = (reason: string): never => {
    log('requests.jsonl', {kind:'blocked', reason});
    process.exit(78);
  };
  pi.on('session_start', (_event, ctx) => {
    const model = ctx.model;
    if (!model || model.provider !== plan.provider || model.id !== plan.model ||
        model.baseUrl.replace(/\/$/,'') !== plan.base_url) stop('effective_model_mismatch');
    log('requests.jsonl', {kind:'model', provider:model.provider, model:model.id,
                           base_url:model.baseUrl, runtime_version:plan.runtime_version});
  });
  pi.on('session_before_compact', () => ({cancel:true}));
  pi.on('before_agent_start', event => {
    log('requests.jsonl', {kind:'effective_prompt', system:event.systemPrompt, prompt:event.prompt});
  });
  pi.on('before_provider_request', event => {
    if (waiting) stop('unrecorded_retry');
    hook++;
    const payload: any = structuredClone(event.payload);
    payload.max_tokens = plan.max_output_tokens;
    if (payload.model !== plan.model || payload.tools?.some((t: any) =>
        ![plan.tool_name,'read'].includes(t.function?.name))) stop('payload_configuration_drift');
    return payload;
  });
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (url !== plan.base_url+'/chat/completions' || init?.method?.toUpperCase() !== 'POST' ||
        typeof init.body !== 'string' || hook === usedHook || waiting) stop('transport_not_permitted');
    usedHook = hook;
    if (++requests > plan.max_requests || Buffer.byteLength(init.body) > plan.max_request_bytes)
      stop('request_limit');
    const payload = JSON.parse(init.body);
    if (payload.model !== plan.model || payload.max_tokens !== plan.max_output_tokens)
      stop('wire_configuration_drift');
    currentRequest = requests;
    waiting = true;
    log('requests.jsonl', {kind:'request', request_index:currentRequest, payload});
    try {
      const response = await nativeFetch(input, {...init, redirect:'error'});
      log('requests.jsonl', {kind:'response', request_index:currentRequest, status:response.status});
      if (!response.ok) stop('provider_http_failure');
      return response;
    } catch {
      return stop('provider_transport_failure');
    }
  };
  pi.on('message_end', event => {
    if (event.message.role === 'assistant') {
      log('requests.jsonl', {kind:'usage', request_index:currentRequest, message:event.message});
      waiting = false;
    }
  });
  function record(id: string, tool: string, args: unknown, stdout: string,
                  exit_code: number, extra: object = {}) {
    log('tools.jsonl', {id, tool, args, stdout, stdout_sha256:sha(stdout), exit_code, request_index:currentRequest, ...extra});
    return {content:[{type:'text' as const,text:stdout}], details:{exit_code,evidence_id:id}};
  }
  function count() { if (++calls > plan.max_tool_calls) stop('tool_limit'); }
  pi.registerTool(defineTool({
    name:plan.tool_name, label:plan.tool_name,
    description:'Read-only frozen CLI replay. Supply argv after the executable. Use --help for supported commands. Responses include evidence_id. This is not a live service.',
    parameters:Type.Object({argv:Type.Array(Type.String(), {maxItems:32})}),
    async execute(id, args) {
      count();
      const processResult = spawnSync(process.env.CERNORA_CAPTURE_PYTHON!,
        ['-m','cernora_reference_workflow.skill_capture','replay',planPath,JSON.stringify(args.argv)],
        {encoding:'utf8', timeout:10000, maxBuffer:2*1024*1024});
      if (processResult.status !== 0) stop('replay_process_failure');
      const reply = JSON.parse(processResult.stdout);
      const stdout = JSON.stringify({evidence_id:id,...reply});
      return record(id,plan.tool_name,args,stdout,reply.exit_code);
    }
  }));
  pi.registerTool(defineTool({
    name:'read', label:'read', description:'Read a frozen Skill file or its referenced resources. Only the installed Skill directory is available.',
    parameters:Type.Object({path:Type.String(), offset:Type.Optional(Type.Integer({minimum:1})),
                           limit:Type.Optional(Type.Integer({minimum:1, maximum:2000}))}),
    async execute(id, args) {
      count();
      let file: string;
      try {
        file = relative(skillRoot, realpathSync(resolve(process.cwd(),args.path)));
        if (file.startsWith('..') || isAbsolute(file) || !(file in plan.skill_files)) throw new Error();
      } catch {
        return record(id,'read',args,'Skill resource unavailable',1);
      }
      const full = readFileSync(resolve(skillRoot,file),'utf8');
      if (full !== plan.skill_files[file]) stop('skill_content_drift');
      const lines = full.split('\n');
      const start = (args.offset ?? 1)-1;
      if (start >= lines.length) return record(id,'read',args,'Line range unavailable',1);
      const end = Math.min(lines.length,start+(args.limit ?? 2000));
      const text = lines.slice(start,end).join('\n');
      if (Buffer.byteLength(text)>65536) return record(id,'read',args,'Use a smaller line range',1);
      return record(id,'read',args,text,0,{file, start_line:start+1,end_line:end,
        full_sha256:sha(full), complete:start===0 && end===lines.length});
    }
  }));
}
