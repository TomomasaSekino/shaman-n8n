'use strict';
const { loadWorkflow, runCode } = require('../helpers/code-node');
const wf = loadWorkflow('SHAMAN_Prompt_Eval.json');
const j = (json) => ({ json });

test('WP認証確認は実際に許可されたドメインを構築する', async () => {
  const valid = await runCode(wf, 'WP: Build Auth Probe', [j({siteId:'example.com'})]);
  expect(valid[0].json.wpUrl).toBe('https://example.com');
  await expect(runCode(wf, 'WP: Build Auth Probe', [j({siteId:'../example.com'})]))
    .rejects.toThrow('WP_TEST_INVALID_SITE_ID');
});

test('WP認証確認は200+ユーザーID以外を認めない', async () => {
  const refs = {'WP: Build Auth Probe':[j({siteId:'example.com',wpUrl:'https://example.com'})]};
  const output = await runCode(wf, 'WP: Parse Auth Probe', [j({statusCode:200,body:{id:12}})], refs);
  expect(output[0].json.wpAuthResult).toEqual({siteId:'example.com',ok:true,userId:12,statusCode:200});
  await expect(runCode(wf,'WP: Parse Auth Probe',[j({statusCode:401,body:{}})],refs))
    .rejects.toThrow('WP_AUTH_TEST_FAILED');
});

test('下書き試験は明示承認と試験専用ドメイン以外では必ず停止', async () => {
  const source = (siteId, option1) => ({
    'Eval: Pick Run':[j({run:{option1,step:'WPD',siteId}})]
  });
  await expect(runCode(wf,'WP: Build Test Draft',[j({siteId:'test.example.com'})],source('test.example.com','NO')))
    .rejects.toThrow('WP_TEST_DRAFT_DENIED');
  await expect(runCode(wf,'WP: Build Test Draft',[j({siteId:'kuro-aji-lab.com'})],source('kuro-aji-lab.com','TEST_DRAFT_ALLOWED')))
    .rejects.toThrow('WP_TEST_DRAFT_DENIED');
  await expect(runCode(wf,'WP: Build Test Draft',[j({siteId:'test-example.com'})],source('test-example.com','TEST_DRAFT_ALLOWED')))
    .rejects.toThrow('WP_TEST_DRAFT_DENIED');
  await expect(runCode(wf,'WP: Build Test Draft',[j({siteId:'test.example.com'})],source('test.example.com','TEST_DRAFT_ALLOWED')))
    .rejects.toThrow('WP_TEST_DRAFT_DENIED');
  // 許可リストは空が既定。承認済み試験ホストを入れた場合だけ正常な下書き入力を作れる。
  const isolated = JSON.parse(JSON.stringify(wf));
  const build = isolated.nodes.find(n => n.name === 'WP: Build Test Draft');
  build.parameters.jsCode = build.parameters.jsCode.replace('var approved = [];', 'var approved = ["test.example.com"];');
  const result = await runCode(isolated,'WP: Build Test Draft',[j({siteId:'test.example.com'})],source('test.example.com','TEST_DRAFT_ALLOWED'));
  expect(result[0].json.wpUrl).toBe('https://test.example.com');
  expect(result[0].json.wpPostBody).toMatchObject({status:'draft',comment_status:'closed',ping_status:'closed'});
});

test('下書き試験は投稿IDとdraft状態だけを成功と判定する', async () => {
  const refs = {'WP: Build Test Draft':[j({siteId:'test.example.com'})]};
  const success = await runCode(wf,'WP: Parse Test Draft',[j({statusCode:201,body:{id:5,status:'draft'}})],refs);
  expect(success[0].json.wpDraftResult).toEqual({ok:true,siteId:'test.example.com',postId:5,status:'draft'});
  await expect(runCode(wf,'WP: Parse Test Draft',[j({statusCode:201,body:{id:6,status:'publish'}})],refs))
    .rejects.toThrow('WP_TEST_DRAFT_FAILED');
});
