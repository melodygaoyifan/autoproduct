// autoproduct block: 微信支付（拉起收银台）— 复制到 utils/wxpay.js，不要重写
// 合规要点：签名必须在服务端完成（预支付订单 unifiedorder/JSAPI 下单），
// 小程序端只拿服务端返回的支付参数拉起 wx.requestPayment；
// 金额一律用「分」为单位的整数，绝不在前端计算价格。
// 用法: const { pay } = require('../../utils/wxpay')
//       await pay(apiBase, { orderId })   // 服务端返回 {timeStamp, nonceStr, package, signType, paySign}
function pay(apiBase, order) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: apiBase + '/api/wx/pay/prepare',
      method: 'POST',
      data: order,
      success(r) {
        const p = r.data || {}
        if (!p.paySign) return reject(new Error('server did not sign the order'))
        wx.requestPayment({
          timeStamp: p.timeStamp, nonceStr: p.nonceStr, package: p.package,
          signType: p.signType || 'RSA', paySign: p.paySign,
          success: resolve, fail: reject,
        })
      },
      fail: reject,
    })
  })
}
module.exports = { pay }
