import type {
  BatchCreateResult,
  BatchGenerationJob,
  TaskGenerateRequest,
} from '@/types/task.d.ts'
import { http } from '@/lib/http'

export async function batchGenerate(data: {
  url?: string
  description?: string
}): Promise<{ message: string; job: BatchGenerationJob }> {
  return await http('/api/tasks/batch-generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function getBatchGenerationJob(jobId: string): Promise<BatchGenerationJob> {
  const result = await http(`/api/tasks/batch-generate-jobs/${jobId}`)
  return result.job
}

export async function batchCreateTasks(
  tasks: TaskGenerateRequest[],
): Promise<{ message: string; results: BatchCreateResult[] }> {
  return await http('/api/tasks/batch-create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tasks }),
  })
}
