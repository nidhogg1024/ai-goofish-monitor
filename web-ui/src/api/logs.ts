import { http } from '@/lib/http'

function buildLogParams(
  fromPos: number,
  taskId?: number | null,
  taskIds?: number[],
): Record<string, number | string> {
  const params: Record<string, number | string> = { from_pos: fromPos }
  if (taskIds && taskIds.length > 0) {
    params.task_ids = taskIds.join(',')
  } else if (taskId !== null && taskId !== undefined) {
    params.task_id = taskId
  }
  return params
}

export async function getLogs(
  fromPos: number = 0,
  taskId?: number | null,
  taskIds?: number[],
): Promise<{ new_content: string; new_pos: number }> {
  const params = buildLogParams(fromPos, taskId, taskIds)
  return await http('/api/logs', { params })
}

export async function clearLogs(taskId?: number | null, taskIds?: number[]): Promise<void> {
  const params: Record<string, number | string> = {}
  if (taskIds && taskIds.length > 0) {
    params.task_ids = taskIds.join(',')
  } else if (taskId !== null && taskId !== undefined) {
    params.task_id = taskId
  }
  await http('/api/logs', { method: 'DELETE', params })
}

export async function getLogTail(
  taskId?: number | null,
  offsetLines: number = 0,
  limitLines: number = 50,
  taskIds?: number[],
): Promise<{ content: string; has_more: boolean; next_offset: number; new_pos: number }> {
  const params: Record<string, number | string> = {
    offset_lines: offsetLines,
    limit_lines: limitLines,
  }
  if (taskIds && taskIds.length > 0) {
    params.task_ids = taskIds.join(',')
  } else if (taskId !== null && taskId !== undefined) {
    params.task_id = taskId
  }
  return await http('/api/logs/tail', { params })
}
