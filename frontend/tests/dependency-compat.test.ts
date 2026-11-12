// Scoped security overrides cross glob/minimatch major versions. Exercise their
// actual consumers so future dependency updates cannot silently break Vercel tracing
// or Nitro archive generation. No network or production data is involved.
import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'
import { finished } from 'node:stream/promises'
import { ZipArchive } from 'archiver'

const require = createRequire(import.meta.url)
const nftRequire = createRequire(require.resolve('@vercel/nft'))
const { glob } = nftRequire('glob')
const { nodeFileTrace } = nftRequire('@vercel/nft')

async function fixture(run: (directory: string) => Promise<void>) {
  const directory = await mkdtemp(join(tmpdir(), 'raphael-dependency-'))
  try {
    await mkdir(join(directory, 'assets', 'ignored'), { recursive: true })
    await writeFile(join(directory, 'assets', 'keep.txt'), 'fixture')
    await writeFile(join(directory, 'assets', 'ignored', 'skip.txt'), 'fixture')
    await writeFile(join(directory, 'helper.mjs'), 'export const value = 1\n')
    await writeFile(join(directory, 'entry.mjs'), 'import { value } from "./helper.mjs"; console.log(value)\n')
    await run(directory)
  } finally { await rm(directory, { recursive: true, force: true }) }
}

test('patched glob preserves the file matching and ignore API used by Nitro tracing', async () => {
  await fixture(async directory => {
    const files = await glob('assets/**/*.txt', { cwd: directory, nodir: true, ignore: ['**/ignored/**'] })
    assert.deepEqual(files, ['assets/keep.txt'])
  })
})

test('Nitro file tracing still follows an entry module and its local imports', async () => {
  await fixture(async directory => {
    const result = await nodeFileTrace([join(directory, 'entry.mjs')], { base: directory, processCwd: directory })
    assert.ok(result.fileList.has('entry.mjs'))
    assert.ok(result.fileList.has('helper.mjs'))
    assert.equal(result.warnings.size, 0)
  })
})

test('archiver still packages selected files through readdir-glob with patched minimatch', async () => {
  await fixture(async directory => {
    const archive = new ZipArchive()
    const output = new PassThrough()
    const entries: string[] = []
    const chunks: Buffer[] = []
    output.on('data', chunk => chunks.push(chunk))
    archive.on('entry', entry => entries.push(entry.name))
    archive.on('warning', error => archive.destroy(error))
    archive.on('error', error => output.destroy(error))
    const complete = finished(output)
    archive.pipe(output)
    archive.glob('assets/**/*.txt', { cwd: directory, ignore: ['**/ignored/**'] })
    await archive.finalize()
    await complete
    assert.deepEqual(entries, ['assets/keep.txt'])
    assert.equal(Buffer.concat(chunks).subarray(0, 2).toString(), 'PK')
  })
})
