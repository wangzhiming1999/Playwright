export type LogLevel = 'action' | 'ok' | 'warn' | 'err' | 'progress' | '';

export interface LogStep {
  stepNumber: number;
  label: string;
  tool?: string;
  lines: string[];
  screenshotFile?: string;
}

const STEP_RE = /^>>> step=(\d+)\s+tool=(\w+)/;
const SCREENSHOT_RE = /\[截图\]\s*(step_\d+_annotated\.\w+)/;

export function getLogLevel(line: string): LogLevel {
  if (line.includes('>>>') || (line.includes('[') && line.includes(']'))) return 'action';
  if (line.includes('✓') || line.includes('✅') || line.includes('成功')) return 'ok';
  if (line.includes('⚠') || line.includes('WARNING')) return 'warn';
  if (line.includes('❌') || line.includes('失败') || line.includes('ERROR')) return 'err';
  if (line.includes('__PROGRESS__')) return 'progress';
  return '';
}

export function parseLogSteps(logs: string[]): LogStep[] {
  const steps: LogStep[] = [];
  let current: LogStep = { stepNumber: 0, label: '初始化', lines: [] };

  for (const line of logs) {
    if (line.startsWith('__PROGRESS__')) continue;

    const stepMatch = line.match(STEP_RE);
    if (stepMatch) {
      if (current.lines.length > 0) steps.push(current);
      const num = parseInt(stepMatch[1], 10);
      const tool = stepMatch[2];
      current = { stepNumber: num, label: `Step ${num} — ${tool}`, tool, lines: [line] };
      continue;
    }

    const ssMatch = line.match(SCREENSHOT_RE);
    if (ssMatch) current.screenshotFile = ssMatch[1];

    current.lines.push(line);
  }

  if (current.lines.length > 0) steps.push(current);
  return steps;
}

export function matchesSearch(line: string, query: string): boolean {
  if (!query) return true;
  return line.toLowerCase().includes(query.toLowerCase());
}
