/**
 * Like Promise.all(items.map(fn)), but never has more than `limit` calls to
 * fn in flight at once. Unbounded Promise.all across a whole folder-drop
 * batch — reading every file into memory (file.arrayBuffer()) and hashing
 * it simultaneously — is real main-thread/memory pressure for anything
 * more than a handful of files, and was the actual cause of the upload
 * dialog feeling janky right after selecting a folder: not any one click
 * handler being slow, but a burst of parallel work competing for the same
 * thread the UI needs to stay responsive.
 */
export async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>
): Promise<R[]> {
  const results: R[] = new Array(items.length);
  let next = 0;

  async function worker() {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  }

  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker());
  await Promise.all(workers);
  return results;
}
