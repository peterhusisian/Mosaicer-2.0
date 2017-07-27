import numpy as np
import cv2
from PIL import Image
import MotionTrack.OpticalFlow.FlowHelper as FlowHelper
class TVL1Flow2:

    IMAGE_GRAD_KERNEL_X = np.array([[-1.0, 0.0, 1.0]], dtype = np.float32)*0.5
    IMAGE_GRAD_KERNEL_Y = np.array([[-1.0],
                                    [0.0],
                                    [1.0]], dtype = np.float32)*0.5
    DIV_P_KERNEL1 = np.array([[-1.0, 1.0]], dtype = np.float32)
    DIV_P_KERNEL2 = np.array([[-1.0],
                              [1.0]], dtype = np.float32)
    FLOW_GRAD_KERNEL_X = np.array([[-1.0, 1.0]], dtype = np.float32)
    FLOW_GRAD_KERNEL_Y = np.array([[-1.0],
                                   [1.0]], dtype = np.float32)
    U_MEDIAN_BLUR_KSIZE = 5
    U_MEDIAN_BLUR_RUN_TIMES = 5

    def __init__(self, frame1, frame2, smooth_weight = 0.15, time_step = 0.25, theta = 0.3, convergence_thresh = 0.01, pyr_scale_factor = 0.5, num_scales = 5, num_warps = 5, max_iter_per_warp = 50):
        self.frame1 = frame1.astype(np.float32)
        self.frame2 = frame2.astype(np.float32)
        self.smooth_weight = smooth_weight
        self.time_step = time_step
        self.theta = theta
        self.convergence_thresh = convergence_thresh
        self.pyr_scale_factor = pyr_scale_factor
        self.num_scales = num_scales
        self.num_warps = num_warps
        self.max_iter_per_warp = max_iter_per_warp
        self.calc_tvl1_flows(self.frame1, self.frame2)

    def calc_tvl1_flows(self, frame1, frame2, U_0_in = None):
        if U_0_in is None:
            U_0_in = np.zeros(frame1.shape[:2] + (2,), dtype = np.float32)
        P1 = np.zeros(U_0_in.shape, dtype = np.float32)
        P2 = np.zeros(P1.shape, dtype = np.float32)
        U = U_0_in.copy()
        for warp_iter in range(0, self.num_warps):
            '''compute I(x+U0) and grad I(x+U0)'''
            U_0 = U.copy()
            warp_frame2, warp_grad_frame2 = self.warp_image_and_gradients_with_U(frame2, U_0)
            cv2.imshow("Warp frame2: ", np.uint8(warp_frame2))

            for iter in range(0, self.max_iter_per_warp):
                '''make sure V is initially set to solving using U, not to just 0's'''
                V = self.solve_V(U, U_0, warp_frame2, warp_grad_frame2, frame1)
                U = self.iterate_U(V, P1, P2)
                P1, P2 = self.iterate_P(U, P1, P2)
                cv2.imshow("Flow angle image: ", FlowHelper.calc_flow_angle_image(U))
                cv2.imshow("Vec image: ", FlowHelper.calc_flow_vector_image(frame1, U))
                cv2.waitKey(1)


    def solve_V(self, U, U_0, warp_image, warp_image_grads, base_image):
        '''https://gyazo.com/3483064fa84a8d84ec4b116cfc522c26
        where U_fit_scores is p(U,U_0)
        and warp_image_grad_mags is |grad I1(x+U_0)|'''
        warp_image_grad_mags = np.linalg.norm(warp_image_grads, axis = 2)
        U_fit_scores = self.calc_U_fit_scores(U, U_0, warp_image, warp_image_grads, base_image)

        threshold_responses = np.zeros(U.shape, dtype = np.float32)
        indices_condition1 = np.where(U_fit_scores < -self.smooth_weight*self.theta*warp_image_grad_mags**2)
        indices_condition2 = np.where(U_fit_scores > self.smooth_weight*self.theta*warp_image_grad_mags**2)
        indices_condition3 = np.where(np.abs(U_fit_scores) <= self.smooth_weight*self.theta*warp_image_grad_mags**2)

        threshold_responses[indices_condition1[0], indices_condition1[1], :] = (self.smooth_weight*self.theta*warp_image_grads)[indices_condition1[0], indices_condition1[1], :]
        threshold_responses[indices_condition2[0], indices_condition2[1], :] = (-self.smooth_weight*self.theta*warp_image_grads)[indices_condition2[0], indices_condition2[1], :]

        '''must deal with cases where gradient is 0 for condition three. The below sets all
        divide by 0 to 0 rather than NaN'''
        condition3_value_mat = (-U_fit_scores[:,:,np.newaxis]*warp_image_grads/(warp_image_grad_mags**2)[:,:,np.newaxis])
        condition3_value_mat[warp_image_grad_mags == 0] = 0
        threshold_responses[indices_condition3[0], indices_condition3[1], :] = condition3_value_mat[indices_condition3[0], indices_condition3[1], :]
        V_new = threshold_responses
        #V_new += U
        return V_new

    def calc_U_fit_scores(self, U, U_0, warp_image, warp_image_grads, base_image):
        '''https://gyazo.com/3956e2d566f73d6e41ccd1a4a3331faf
        where grad I1(x+U_0) is warp_image_grads,
        I1(x+U_0) is warp_image
        I0(x) is base_image'''
        U_sub_U_0 = U - U_0
        grad_dot_x = warp_image_grads[:,:,0]*U_sub_U_0[:,:,0]
        grad_dot_y = warp_image_grads[:,:,1]*U_sub_U_0[:,:,1]
        grad_dots = grad_dot_x + grad_dot_y
        U_fit_scores = warp_image + grad_dots - base_image
        return U_fit_scores

    def iterate_U(self, V, P1, P2):
        '''https://gyazo.com/22963adae038465f941fc79dac206813'''
        add_x = self.div_P(P1)
        add_y = self.div_P(P2)
        add_xy = np.dstack((add_x, add_y))
        '''could try to use LinearLOESS smoothing'''
        U_new = V + add_xy
        for i in range(0, TVL1Flow2.U_MEDIAN_BLUR_RUN_TIMES):
            U_blurred_channel1 = cv2.medianBlur(U_new[:,:,0], TVL1Flow2.U_MEDIAN_BLUR_KSIZE)
            U_blurred_channel2 = cv2.medianBlur(U_new[:,:,1], TVL1Flow2.U_MEDIAN_BLUR_KSIZE)
            U_new = np.dstack((U_blurred_channel1, U_blurred_channel2))
        return U_new

    def iterate_P(self, U, P1, P2):
        '''https://gyazo.com/861d9741eaa09efedef2601317a00027'''
        '''other source used max(1, numerator) rather than just 1+numerator.
        May want to try'''
        U_x_grad_xy, U_y_grad_xy = self.calc_2D_flow_gradients(U)
        P1_new = (P1 + (self.time_step/self.theta) * U_x_grad_xy)/(1.0 + (self.time_step/self.theta) * np.linalg.norm(U_x_grad_xy, axis = 2)[:,:,np.newaxis])
        P2_new = (P2 + (self.time_step/self.theta) * U_y_grad_xy)/(1.0 + (self.time_step/self.theta) * np.linalg.norm(U_y_grad_xy, axis = 2)[:,:,np.newaxis])
        return P1_new, P2_new

    def calc_2D_flow_gradients(self, flows_xy):
        '''https://gyazo.com/1f07eeff8af568a030af6120695f8727'''
        flows_x_grad_xy = self.calc_1D_flow_gradients(flows_xy[:,:,0])
        flows_y_grad_xy = self.calc_1D_flow_gradients(flows_xy[:,:,1])
        return flows_x_grad_xy, flows_y_grad_xy

    def calc_1D_flow_gradients(self, flows):
        '''Computes https://gyazo.com/1f07eeff8af568a030af6120695f8727
        for a single component of flow'''
        grad_x = cv2.filter2D(flows, cv2.CV_32F, TVL1Flow2.FLOW_GRAD_KERNEL_X)
        grad_y = cv2.filter2D(flows, cv2.CV_32F, TVL1Flow2.FLOW_GRAD_KERNEL_Y)
        '''handles border cases'''
        grad_x[grad_x.shape[0]-1, :] = np.zeros(grad_x.shape[1])
        grad_y[:, grad_y.shape[1]-1] = np.zeros(grad_y.shape[0])
        grad_xy = np.dstack((grad_x, grad_y))
        return grad_xy


    def div_P(self, P):
        '''https://gyazo.com/4ba596385ac9ad397baa963f4261259d'''
        kernel_response1 = cv2.filter2D(P[:,:,0], cv2.CV_32F, TVL1Flow2.DIV_P_KERNEL1)
        '''handles boundary conditions. Not sure if values are reversed due to xy flip
        on images and P...'''
        kernel_response1[0,:] = P[0,:,0]
        kernel_response1[kernel_response1.shape[0]-1, :] = -P[kernel_response1.shape[0]-1,:,0]

        kernel_response2 = cv2.filter2D(P[:,:,1], cv2.CV_32F, TVL1Flow2.DIV_P_KERNEL2)
        '''handles boundary conditions. Not sure if values are reversed due to xy flip
        on images and P...'''
        kernel_response2[:,0] = P[:,0,1]
        kernel_response2[:,kernel_response2.shape[1]-1] = -P[:,kernel_response2.shape[1]-1,1]
        return kernel_response1 + kernel_response2

    def warp_image_and_gradients_with_U(self, image, U):
        '''not sure if warp first then calc gradients, or calc gradients,
        then warp both image and gradients. If algorithm not working, give
        this a try'''
        warp_image = self.warp_image_with_U(image, U)
        image_grad_xy = self.calc_image_gradients(image)
        warp_image_grad_xy = self.warp_image_with_U(image_grad_xy, U)
        #warp_image_grad_xy = self.calc_image_gradients(warp_image)
        return warp_image, warp_image_grad_xy

    def warp_image_with_U(self, image, U):
        x_indices_mat = np.array([np.arange(image.shape[1]) for j in range(0, image.shape[0])])
        y_indices_mat = np.array([np.arange(image.shape[0]) for j in range(0, image.shape[1])]).T
        x_warp_map = (x_indices_mat + U[:,:,0]).astype(np.float32)
        y_warp_map = (y_indices_mat + U[:,:,1]).astype(np.float32)
        warp_image = cv2.remap(image, x_warp_map, y_warp_map, cv2.INTER_CUBIC)
        return warp_image.astype(np.float32)

    def calc_image_gradients(self, image):
        grad_x = cv2.filter2D(image, cv2.CV_32F, TVL1Flow2.IMAGE_GRAD_KERNEL_X)
        grad_y = cv2.filter2D(image, cv2.CV_32F, TVL1Flow2.IMAGE_GRAD_KERNEL_Y)
        '''need to add neumannn boundary conditions'''
        grad_xy = np.dstack((grad_x, grad_y))
        return grad_xy