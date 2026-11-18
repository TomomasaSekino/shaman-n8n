'use strict';
// サイト未登録・参照画像0枚の実行経路。外部APIは呼び出さない。
const { loadWorkflow, runCode } = require('../helpers/code-node');
const wf = loadWorkflow();
const j = (json) => ({ json });

test('サイトURLは必ず SITE_ID と一致し、WP送信前に検証される', async () => {
  const valid = j({siteId: 'new-site.example', wpUrl: 'https://new-site.example'});
  for (const node of ['WP: Validate Upload Target', 'WP: Validate Post Target']) {
    const out = await runCode(wf, node, [valid]);
    expect(out).toHaveLength(1);
    await expect(runCode(wf, node, [j({siteId: 'new-site.example', wpUrl: 'https://other.example'})]))
      .rejects.toThrow('WP_SITE_ID_INVALID');
    await expect(runCode(wf, node, [j({siteId: '../other', wpUrl: 'https://../other'})]))
      .rejects.toThrow('WP_SITE_ID_INVALID');
  }
});

test('Google Driveフォルダーが無い場合は参照画像0枚として続行する', async () => {
  const source = j({siteId:'new-site.example', imgFolder:'new-site.example', articleHtml:'<h2>例</h2>'});
  const result = await runCode(wf,'Read Reference Images',[j({folderId:'',folderName:'new-site.example'})], {
    'Check P6 Mode':[source],
    'P6: Check Image Folder':[j({ folderId:'', folderName:'new-site.example',
      imageReferenceNote:'参照画像なし（フォルダ無し）' })],
  });
  expect(result[0].json.referenceImages).toEqual([]);
  expect(result[0].json.siteId).toBe('new-site.example');
});

test('画像0枚はimages/generationsへ、1枚以上ならimages/editsへ送る', async () => {
  const section = j({siteId:'new-site.example', wpUrl:'https://new-site.example', sectionIndex:1,
    imageDescription:'山の稜線', IMAGE_QUALITY:'high'});
  const none = await runCode(wf, 'Build Image Requests', [section], {
    'Read Reference Images': [j({referenceImages: []})],
  });
  expect(none[0].json.imageApiPath).toBe('https://api.openai.com/v1/images/generations');
  expect(none[0].json.requestBody.images).toBeUndefined();
  const withRef = await runCode(wf, 'Build Image Requests', [section], {
    'Read Reference Images': [j({referenceImages: [{b64:'YQ==',mime:'image/png',file:'a.png'}]})],
  });
  expect(withRef[0].json.imageApiPath).toBe('https://api.openai.com/v1/images/edits');
  expect(withRef[0].json.requestBody.images).toHaveLength(1);
});
