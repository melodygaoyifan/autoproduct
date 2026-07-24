// autoproduct block: 订阅消息 — 复制到 utils/subscribe.js，不要重写
// 合规要点：requestSubscribeMessage 必须由用户点击直接触发（不能在 onLoad 里调），
// 模板 id 在小程序后台申请；一次授权一次下发。
// 用法: const { askSubscribe } = require('../../utils/subscribe')
//       askSubscribe(['TEMPLATE_ID'])  // 绑定在按钮的事件处理里
function askSubscribe(templateIds) {
  return new Promise((resolve) => {
    wx.requestSubscribeMessage({
      tmplIds: templateIds,
      success: (res) => resolve(res),
      fail: () => resolve({}),
    })
  })
}
module.exports = { askSubscribe }
