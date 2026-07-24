// autoproduct block: 微信登录 — 预置模块，复制到 utils/wxlogin.js，不要重写
// 用法: const { ensureLogin } = require('../../utils/wxlogin')
//       const openidToken = await ensureLogin(API_BASE)
// 服务端需实现 POST /api/wx/login {code} -> {token}（code2session 换取 openid）
function ensureLogin(apiBase) {
  return new Promise((resolve, reject) => {
    const cached = wx.getStorageSync('ap_token')
    if (cached) return resolve(cached)
    wx.login({
      success(res) {
        if (!res.code) return reject(new Error('wx.login failed'))
        wx.request({
          url: apiBase + '/api/wx/login',
          method: 'POST',
          data: { code: res.code },
          success(r) {
            const token = r.data && r.data.token
            if (!token) return reject(new Error('login exchange failed'))
            wx.setStorageSync('ap_token', token)
            resolve(token)
          },
          fail: reject,
        })
      },
      fail: reject,
    })
  })
}
module.exports = { ensureLogin }
